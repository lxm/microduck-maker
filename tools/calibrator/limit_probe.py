"""Bounded single-joint contact repeatability measurement; RAM writes only.

A stationary joint is a candidate contact, never proof of a mechanical stop.
No zero calculation or calibration commit is reachable from this operation.
"""
import copy
import math
import statistics
import time

from core import CalibrationError, IDS, read, sid_value, word, write_checked

COUNTS_PER_DEG=4096/360
ACTIVE=('arming','seeking','returning')
HEARTBEAT_SECONDS=2.0


class LimitProbe:
    def __init__(self,session,*,sid=None,direction=None,travel_deg=10,speed_deg_s=1,
                 torque_limit=100,repeats=3,confirmed=False):
        sid=sid_value(sid)
        if confirmed is not True:
            raise CalibrationError('请确认躯干固定、其余关节有支撑、方向和搜索范围已检查、手已离开')
        if session.held:
            raise CalibrationError('限位测量前必须全部断力；每次只驱动选中的一颗')
        if type(direction) is not int or direction not in (-1,1):
            raise CalibrationError('请选择编码器增大或减小的探测方向')
        for value,low,high,label in ((travel_deg,3,20,'搜索行程'),(speed_deg_s,.5,2,'速度')):
            if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
                raise CalibrationError(f'{label}超出允许范围 {low}—{high}')
        if type(torque_limit) is not int or not 50<=torque_limit<=200:
            raise CalibrationError('探测输出上限需50—200，不能自动加力')
        if type(repeats) is not int or not 2<=repeats<=5:
            raise CalibrationError('重复次数需2—5')
        before=session.checked_snapshot()
        origin=word(before[str(sid)],56)
        end=origin+direction*math.floor(travel_deg*COUNTS_PER_DEG)
        if not 0<=end<=4095:
            raise CalibrationError('搜索会跨越编码器单圈边界，请缩小行程或调整起点')
        cap=min(torque_limit,word(before[str(sid)],48))
        if cap<50:raise CalibrationError('原有输出上限低于50，保留原值并取消探测')
        self.s=session
        self.before=copy.deepcopy(before)
        self.result=dict(id=sid,phase='arming',direction=direction,origin_raw=origin,end_raw=end,
            travel_deg=travel_deg,speed_deg_s=speed_deg_s,torque_limit=cap,repeats=repeats,
            samples=[],statistics=None,position_raw=origin,goal_raw=origin,elapsed_s=0,
            eeprom_unchanged=None,note='仅测量疑似接触点；重复性不等于零位准确度')
        self.started=self.last_tick=self.last_goal=time.monotonic()
        self.phase_started=self.started
        self.window=[];self.settled_since=None;self.wait_since=None
        self.deadline=self.started+min(300,repeats*(2*travel_deg/speed_deg_s+8)+5)
        session.save('limit-probe-start',dict(measurement=copy.deepcopy(self.result),registers=before))
        session.current=before;session.pending=None
        session.bus.measurement_only=True

    def running(self):return self.result['phase'] in ACTIVE

    def verify_eeprom(self,rows):
        unchanged=all(rows[str(s)][:40]==self.before[str(s)][:40]
                      and rows[str(s)][55]==self.before[str(s)][55] for s in IDS)
        self.result['eeprom_unchanged']=unchanged
        if not unchanged:
            self.s.fault='探测期间 EEPROM 或锁状态发生变化，请保留记录并检查'
            raise CalibrationError(self.s.fault)

    def goal(self,target):
        r=self.result;sid=r['id']
        if not min(r['origin_raw'],r['end_raw'])<=target<=max(r['origin_raw'],r['end_raw']):
            raise CalibrationError('目标超出本次搜索窗口')
        self.s.zero_goal(sid,target)
        r['goal_raw']=target
        # Verify each potentially arming write, including a missing ACK.
        self.s.current=self.s.checked_snapshot()

    def finish(self,phase,error=None):
        self.result['phase']=phase
        if error:self.result['error']=str(error)
        try:self.s.relax_all()
        except Exception as exc:
            self.result['phase']='failed'
            self.result['error']=(str(error)+'；' if error else '')+str(exc)
            raise
        finally:
            self.s.save('limit-probe-ended',copy.deepcopy(self.result))

    def abort(self,reason):
        if self.running():self.finish('failed',reason)

    def advance(self):
        if not self.running():return
        try:self._advance()
        except Exception as exc:
            self.finish('failed',exc)
            raise

    def _advance(self):
        s=self.s;r=self.result;sid=r['id'];now=time.monotonic()
        # Delayed control never catches up with a large goal jump.
        if now-self.last_tick>HEARTBEAT_SECONDS:
            raise CalibrationError('本机探测循环超过2秒未更新，已取消')
        self.last_tick=now;r['elapsed_s']=round(now-self.started,2)
        if now>self.deadline:raise CalibrationError('探测总时间超限')
        s.current=s.checked_snapshot();self.verify_eeprom(s.current)
        row=s.current[str(sid)];position=word(row,56);goal=word(row,42)
        r['position_raw']=position
        # Leave a tiny feedback tolerance, never allow wraparound or large jumps.
        lo,hi=sorted((r['origin_raw'],r['end_raw']))
        if not lo-4<=position<=hi+4:
            raise CalibrationError('实测位置离开搜索窗口，请检查关节支撑或外力')
        if r['phase']=='arming':
            if abs(position-r['origin_raw'])>2:raise CalibrationError('起点发生移动，取消探测')
            for addr,data in ((48,r['torque_limit'].to_bytes(2,'little')),(50,[5]),(51,[20])):
                write_checked(s.bus,sid,addr,data,holding=True)
                s.current[str(sid)][addr:addr+len(data)]=list(data)
            samples=[word(read(s.bus,sid,56,2),0) for _ in range(3)]
            if any(abs(p-position)>2 for p in samples):raise CalibrationError('设置参数时关节移动，取消使能')
            self.goal(samples[-1])
            r['phase']='seeking';self.last_goal=self.phase_started=time.monotonic()
            return
        error=goal-position
        if abs(error)>16:raise CalibrationError('跟随偏差超过16计数，停止探测；不自动增加输出')
        if now-self.phase_started>r['travel_deg']/r['speed_deg_s']+8:
            raise CalibrationError('本轮移动超时，无法可靠识别接触点')
        if r['phase']=='seeking':
            self.window.append((now,position))
            # Permit slow but fresh serial/UI sampling; four real observations
            # must fit even when a cycle takes longer than the nominal 100 ms.
            self.window=[x for x in self.window if now-x[0]<=1.6]
            stationary=(len(self.window)>=4 and now-self.window[0][0]>=.45
                        and max(x[1] for x in self.window)-min(x[1] for x in self.window)<=2)
            if r['direction']*error>=8 and stationary:
                contact=round(statistics.median(x[1] for x in self.window))
                if r['direction']*(contact-r['origin_raw'])<12:
                    raise CalibrationError('未观察到至少12计数的有效运动；可能已顶住、摩擦过大或输出不足')
                # Remove the forward error immediately, before durable logging.
                self.goal(position)
                sample=dict(round=len(r['samples'])+1,position_raw=contact,
                    from_start_deg=(contact-r['origin_raw'])/COUNTS_PER_DEG,
                    load_raw=word(row,60),current_raw=word(row,69),voltage=row[62]/10,
                    temperature=row[63],goal_error_counts=error)
                r['samples'].append(sample)
                values=[x['position_raw'] for x in r['samples']]
                spread=max(values)-min(values);std=statistics.pstdev(values)
                r['statistics']=dict(count=len(values),mean_raw=statistics.mean(values),
                    min_raw=min(values),max_raw=max(values),spread_counts=spread,
                    spread_deg=spread/COUNTS_PER_DEG,std_counts=std,std_deg=std/COUNTS_PER_DEG)
                r['phase']='returning';self.phase_started=self.last_goal=now
                self.settled_since=self.wait_since=None;self.window=[]
                s.save('limit-probe-contact',dict(sample=sample,statistics=r['statistics']))
                return
            if goal==r['end_raw'] and abs(error)<=4:
                if self.settled_since is None:self.settled_since=now
                if now-self.settled_since>=.5:
                    self.finish('no_contact','已到搜索窗口终点，未发现疑似限位；不会继续扩大行程')
                return
            self.settled_since=None
            # Cap the commanded preload while the contact window is evaluated.
            if r['direction']*error>=8:
                self.last_goal=now
                return
            target=r['end_raw']
        else:
            target=r['origin_raw']
            if goal==target and abs(position-target)<=3:
                if self.settled_since is None:self.settled_since=now
                if now-self.settled_since>=.3:
                    if len(r['samples'])>=r['repeats']:
                        self.finish('complete')
                    else:
                        r['phase']='seeking';self.phase_started=self.last_goal=now
                        self.window=[];self.settled_since=None
                return
            self.settled_since=None
            if abs(error)>=8:
                if self.wait_since is None:self.wait_since=now
                if now-self.wait_since>1.5:raise CalibrationError('退回起点时无法跟随，已停止')
                self.last_goal=now
                return
            self.wait_since=None
        step=min(4,int(max(0,now-self.last_goal)*r['speed_deg_s']*COUNTS_PER_DEG))
        if step:
            self.goal(goal+max(-step,min(step,target-goal)))
            self.last_goal=time.monotonic()
