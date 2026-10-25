"""HD1910 EEPROM calibration, individual holding and bounded zero inspection."""
import copy
import math
import secrets
import statistics
import time

IDS = [10, 11, 12, 13, 14, 20, 21, 22, 23, 24, 30, 31, 32, 33, 34]
BODY_IDS = IDS[:-1]
NAMES = dict(zip(IDS, ['右髋转向','右髋侧摆','右髋俯仰','右膝','右踝',
    '左髋转向','左髋侧摆','左髋俯仰','左膝','左踝','颈部俯仰','头部俯仰','头部转向','头部侧摆','嘴部']))
HOME = dict(zip(IDS, [0,5,26.24,.28,-25.95,0,-5,-26.24,-.28,25.95,20,20,0,0,0]))
LIMITS = dict(zip(IDS, [(-30,25),(-22,22),(-90,90),(-90,90),(-70,70),
    (-25,30),(-22,22),(-90,90),(-90,90),(-70,70),(-90,70),(-90,90),(-170,170),(-20,20),(-5,30)]))

class CalibrationError(RuntimeError):
    pass

def sid_value(value):
    if type(value) is not int or value not in IDS:
        raise CalibrationError('无效舵机编号；不接受广播 ID')
    return value

def word(r, a):
    return int.from_bytes(bytes(r[a:a+2]), 'little')

def signed(value):
    return -(value & 0x7fff) if value & 0x8000 else value

def circular(value):
    return (value + 2048) % 4096 - 2048

def encode_offset(value):
    if type(value) is not int or not -2047 <= value <= 2047:
        raise CalibrationError('偏移超出 HD1910 单圈标定范围，需重新核对装配')
    return (0x8000 | -value) if value < 0 else value

def read(bus, sid, addr, length):
    error, data = bus.read(sid, addr, length)
    if error or len(data) != length:
        raise CalibrationError(f'ID{sid} 读取失败：状态 {error}，长度 {len(data)}/{length}')
    return list(data)

def write_checked(bus, sid, addr, data, *, holding=False):
    # Calibration defaults to EEPROM-only. The separate explicit hold path opts
    # into current-position holding because mode 4 can arm on a goal write.
    sid_value(sid)
    allowed = (addr == 31 and len(data) == 2) or (addr == 55 and list(data) in ([0],[1])) or (addr == 40 and list(data) == [0])
    if holding:
        allowed |= (addr in (42,48) and len(data)==2) or (addr in (50,51) and len(data)==1) or (addr==40 and list(data)==[1])
    if not allowed:
        raise CalibrationError('标定工具禁止写运动目标、通力、PID或其他寄存器')
    error = bus.write(sid, addr, bytes(data))
    if error not in (None, 0):
        raise CalibrationError(f'ID{sid} 写入状态 {error}')
    time.sleep(.025)
    if read(bus, sid, addr, len(data)) != list(data):
        raise CalibrationError(f'ID{sid} 地址{addr}写后读回不一致')

def snapshot(bus, held=()):
    rows = {str(sid): read(bus, sid, 0, 80) for sid in IDS}
    for sid in IDS:
        r = rows[str(sid)]
        if r[3:7] != [10,31,sid,0] or r[33] != 4:
            raise CalibrationError(f'ID{sid} 的型号/编号/模式与本机 HD1910 不符')
        if r[40] != int(sid in held):
            raise CalibrationError(f'ID{sid} 通力状态与标定会话不符')
        if r[55] != 1:
            raise CalibrationError(f'ID{sid} EEPROM 未上锁，先核对现有维护操作')
        if r[65] != 0 or r[63] >= 48 or not 60 <= r[62] <= 84:
            raise CalibrationError(f'ID{sid} 状态/温度/电压不满足标定条件')
        if word(r,56) > 4095 or abs(signed(word(r,31))) > 2047:
            raise CalibrationError(f'ID{sid} 不在本工具支持的单圈坐标范围')
    return rows

def set_offset(bus, sid, value):
    if read(bus,sid,40,1) != [0]:
        raise CalibrationError('写零位前发现通力，操作已取消')
    try:
        write_checked(bus,sid,55,[0])
        write_checked(bus,sid,31,int(value).to_bytes(2,'little'))
    finally:
        write_checked(bus,sid,55,[1])

def transaction(bus, sid, target, before, persist, held=()):
    """One offset only; lost ACK or failed config save triggers readback rollback."""
    sid_value(sid)
    if sid in held:
        raise CalibrationError('先释放当前这颗，再修改零位')
    original = word(before[str(sid)],31)
    try:
        if target != original:
            set_offset(bus,sid,target)
        after = snapshot(bus,held)
        for other in IDS:
            for addr in range(56):
                if other == sid and addr in (31,32):
                    continue
                if after[str(other)][addr] != before[str(other)][addr]:
                    raise CalibrationError(f'非预期寄存器变化：ID{other}/{addr}')
        expected = (word(before[str(sid)],56)+signed(original)-signed(target)) % 4096
        if abs(circular(word(after[str(sid)],56)-expected)) > 4:
            raise CalibrationError('写入期间关节发生移动或位置换算不符')
        persist(after)
        return after
    except Exception as error:
        failures=[]
        # Turning OFF is the only torque operation this tool can issue.
        for other in IDS:
            try:
                if read(bus,other,40,1) != [0]:
                    write_checked(bus,other,40,[0])
            except Exception as exc:
                failures.append(f'ID{other}断力核验：{exc}')
        try:
            if word(read(bus,sid,31,2),0) != original:
                set_offset(bus,sid,original)
            snapshot(bus)
        except Exception as exc:
            failures.append(f'零位回滚：{exc}')
        if failures:
            raise CalibrationError(f'{error}；状态待恢复：'+ '; '.join(failures)) from error
        raise CalibrationError(f'{error}；已恢复该关节原零位，保持断力') from error

class Session:
    def __init__(self, bus, save, config_commit, mouth=None):
        self.bus,self.save,self.config_commit=bus,save,config_commit
        self.initial=snapshot(bus)
        self.current=copy.deepcopy(self.initial)
        self.mouth=copy.deepcopy(mouth)
        self.initial_mouth=copy.deepcopy(mouth)
        self.pending=None
        self.mouth_closed=None
        self.completed={}
        self.held=set()
        self.zero_check=None
        self.fault=None
        self.verified_at=time.time()
        self.save('initial',dict(registers=self.initial,mouth=mouth,time=time.time()))

    def checked_snapshot(self):
        if self.fault:raise CalibrationError('会话已停止写入：'+self.fault)
        now=snapshot(self.bus,self.held)
        for sid in IDS:
            # EEPROM and actuator settings must not change outside this session.
            if now[str(sid)][:56] != self.current[str(sid)][:56]:
                raise CalibrationError(f'ID{sid} 配置被其他操作改变；停止标定并重新检查')
        self.verified_at=time.time()
        return now

    def status(self):
        return dict(all_torque_off=not self.held,held=sorted(self.held),fault=self.fault,verified_at=self.verified_at,completed=self.completed,pending=self.pending,
            zero_check=copy.deepcopy(self.zero_check),zero_inspection_supported=True,
            mouth_closed=self.mouth_closed,servos=[dict(id=sid,name=NAMES[sid],
                position=word(self.current[str(sid)],56),offset_raw=word(self.current[str(sid)],31),
                angle_deg=(2048-word(self.current[str(sid)],56))*360/4096,
                voltage=self.current[str(sid)][62]/10,temperature=self.current[str(sid)][63],
                hold_position=word(self.current[str(sid)],42) if sid in self.held else None,
                initial_offset_raw=word(self.initial[str(sid)],31),home=HOME[sid],limits=LIMITS[sid],
                direction=-1 if sid!=34 else None) for sid in IDS])

    def sample(self,sid,known_angle=0,endpoint=None):
        self.require_idle()
        sid=sid_value(sid)
        if sid in self.held:raise CalibrationError('这一颗正在保持，请先释放再采样')
        if type(known_angle) not in (int,float) or not math.isfinite(known_angle) or not LIMITS[sid][0] <= known_angle <= LIMITS[sid][1]:
            raise CalibrationError('已知角度无效或超出当前候选范围')
        if sid==34 and endpoint not in ('closed','open'):
            raise CalibrationError('嘴部需要依次采样自然闭合、舒适张开两个端点')
        self.pending=None
        now=self.checked_snapshot()
        values=[]
        for _ in range(20):
            values.append(word(read(self.bus,sid,56,2),0));time.sleep(.04)
        if max(values)-min(values)>4 or any(x>4095 for x in values):
            raise CalibrationError('关节仍在移动，请保持这一颗不动再采样')
        if read(self.bus,sid,40,1)!=[0]:
            raise CalibrationError('采样期间通力状态改变')
        position=round(statistics.median(values));old=word(now[str(sid)],31)
        sample=dict(id=sid,position=position,old_offset_raw=old,time=time.time(),samples=values)
        if sid==34 and endpoint=='closed':
            self.mouth_closed=sample
            self.save('mouth-closed',sample)
            return dict(closed_captured=True,position=position)
        if sid==34:
            closed=self.mouth_closed
            if not closed or closed['old_offset_raw']!=old or time.time()-closed['time']>600:
                raise CalibrationError('请先重新采样闭嘴位置')
            travel=position-closed['position']
            if not -1024<=travel<=-16:
                raise CalibrationError('本机嘴部张开应使计数减小，行程需16—1024；请核对端点')
            delta=closed['position']-2048
            new=encode_offset(circular(signed(old)+delta))
            mouth=dict(schema=1,id=34,mode=4,eeprom_offset_raw=new,closed_raw=2048,open_raw=2048+travel)
        else:
            target=round(2048-known_angle*4096/360)
            if not 0<=target<=4095:
                raise CalibrationError('参考角度不能编码为单圈目标')
            delta=position-target
            new=encode_offset(circular(signed(old)+delta));mouth=None
        self.current=now
        self.pending=dict(**sample,token=secrets.token_hex(16),known_angle=known_angle,
            new_offset_raw=new,correction_counts=circular(signed(new)-signed(old)),mouth=mouth)
        self.save('sample',self.pending)
        return self.pending

    def apply(self,token):
        self.require_idle()
        p=self.pending
        if not p or not isinstance(token,str) or not secrets.compare_digest(token,p['token']):
            raise CalibrationError('采样已失效，请重新采样')
        self.pending=None  # Never replay a write after an HTTP/SSH retry.
        if time.time()-p['time']>30:
            raise CalibrationError('采样超过30秒，请保持关节位置重新采样')
        now=self.checked_snapshot();sid=p['id']
        if abs(circular(word(now[str(sid)],56)-p['position']))>4:
            raise CalibrationError('采样后这一颗关节移动了，请重新采样')
        self.save('intent',p)
        next_mouth=p['mouth'] if sid==34 else self.mouth
        after=transaction(self.bus,sid,p['new_offset_raw'],now,
            lambda rows:self.config_commit(rows,next_mouth),self.held)
        self.current=after;self.mouth=copy.deepcopy(next_mouth)
        self.completed[str(sid)]=dict(time=time.time(),known_angle=p['known_angle'],offset_raw=p['new_offset_raw'])
        self.save('applied',dict(id=sid,registers=after,mouth=self.mouth,completed=self.completed))
        return self.status()

    def undo(self,sid):
        self.require_idle()
        sid=sid_value(sid);self.pending=None
        now=self.checked_snapshot();target=word(self.initial[str(sid)],31)
        mouth=self.initial_mouth if sid==34 else self.mouth
        self.save('undo-intent',dict(id=sid,target=target))
        self.current=transaction(self.bus,sid,target,now,lambda rows:self.config_commit(rows,mouth),self.held)
        self.mouth=copy.deepcopy(mouth);self.completed.pop(str(sid),None)
        self.save('undone',dict(id=sid,registers=self.current,mouth=self.mouth))
        return self.status()

    def hold(self,sid):
        self.require_idle()
        sid=sid_value(sid)
        if sid in self.held:raise CalibrationError('这一颗已经在保持')
        if str(sid) not in self.completed:raise CalibrationError('先保存这一颗的标定，再单独使能')
        now=self.checked_snapshot();r=now[str(sid)]
        position=word(r,56)
        target=(self.mouth['open_raw'] if sid==34 else round(2048-self.completed[str(sid)]['known_angle']*4096/360))
        if abs(circular(position-target))>4:
            raise CalibrationError('这一颗已离开刚标定的位置，请手动摆回后再使能；工具不会驱动它归零')
        samples=[word(read(self.bus,sid,56,2),0) for _ in range(3)]
        if any(abs(circular(x-position))>2 for x in samples):raise CalibrationError('关节在移动，取消使能')
        self.save('hold-intent',dict(id=sid,position=position,p=10 if sid==34 else 5,d=20,torque_limit=300))
        try:
            # Mode 4 may arm on the goal write itself: set bounded holding gains
            # first, then only write the freshly measured position, never HOME.
            for addr,data in [(48,(300).to_bytes(2,'little')),(50,[10 if sid==34 else 5]),(51,[20])]:
                write_checked(self.bus,sid,addr,data,holding=True)
            if read(self.bus,sid,40,1)!=[0]:raise CalibrationError('写目标前通力状态改变')
            latest=word(read(self.bus,sid,56,2),0)
            if abs(circular(latest-position))>2:raise CalibrationError('设置保持参数时关节移动')
            self.held.add(sid)  # Track before the potentially arming write, including lost ACK.
            write_checked(self.bus,sid,42,latest.to_bytes(2,'little'),holding=True)
            if read(self.bus,sid,40,1)==[0]:write_checked(self.bus,sid,40,[1],holding=True)
            after=snapshot(self.bus,self.held)
            for other in IDS:
                for addr in range(56):
                    if other==sid and addr in (40,42,43,48,49,50,51):continue
                    if after[str(other)][addr]!=now[str(other)][addr]:raise CalibrationError('使能时其他参数发生变化')
            if abs(circular(word(after[str(sid)],56)-latest))>8:raise CalibrationError('保持后位置偏差过大')
            self.current=after;self.verified_at=time.time();self.save('held',dict(id=sid,registers=after))
            return self.status()
        except Exception:
            self.relax_all()
            raise

    def release(self,sid):
        self.require_idle()
        if self.zero_check and self.zero_check['phase']=='holding':self.zero_check['phase']='cancelled'
        sid=sid_value(sid)
        write_checked(self.bus,sid,40,[0])
        self.held.discard(sid)
        # Goal is deliberately NOT restored: writing an old goal can arm mode 4.
        for addr,n in [(48,2),(50,1),(51,1)]:
            write_checked(self.bus,sid,addr,self.initial[str(sid)][addr:addr+n],holding=True)
        self.current=snapshot(self.bus,self.held);self.verified_at=time.time()
        self.pending=None;self.save('released',dict(id=sid,registers=self.current))
        return self.status()

    def relax_all(self):
        # Cancel first: a later status poll must never resume an interrupted move.
        if self.zero_check and self.zero_check['phase'] in ('arming','moving','holding'):
            self.zero_check['phase']='cancelled'
        errors=[];self.pending=None
        # Attempt every ID even when one does not answer. No early abort.
        for sid in IDS:
            try:write_checked(self.bus,sid,40,[0])
            except Exception as e:errors.append(f'ID{sid}: {e}')
        if not errors:
            for sid in IDS:
                try:
                    for addr,n in [(48,2),(50,1),(51,1)]:
                        write_checked(self.bus,sid,addr,self.initial[str(sid)][addr:addr+n],holding=True)
                except Exception as e:errors.append(f'ID{sid} 参数恢复: {e}')
        if errors:
            self.fault='断力/恢复未全部确认；保持托稳并切断主电源。'+'；'.join(errors)
            raise CalibrationError(self.fault)
        self.held.clear();self.current=snapshot(self.bus);self.verified_at=time.time()
        self.save('all-off',dict(registers=self.current))
        return self.status()

    def refresh(self):
        self.current=self.checked_snapshot()
        for sid in self.held:
            r=self.current[str(sid)]
            if abs(circular(word(r,56)-word(r,42)))>46:
                raise CalibrationError(f'ID{sid} 保持偏差超过4°；'+self.following_detail(sid))
        if self.zero_check:
            self.zero_check['max_error_deg']=max(abs(word(self.current[str(s)],56)-2048)*360/4096 for s in self.zero_check.get('ids',IDS))
        return self.status()

    def following_detail(self,sid):
        r=self.current[str(sid)]
        return (f'目标 {word(r,42)}，实测 {word(r,56)}，'
            f'差 {abs(circular(word(r,56)-word(r,42)))*360/4096:.2f}°，'
            f'P{r[50]}/D{r[51]}，扭矩上限 {word(r,48)}，电压 {r[62]/10:.1f}V；'
            '请检查是否受力或卡住，不要强掰关节')

    def zero_running(self):
        return self.zero_check is not None and self.zero_check['phase'] in ('arming','moving')

    def require_idle(self):
        if self.zero_running():
            raise CalibrationError('正在归零，请先全部断力，再进行单关节操作')

    def start_zero(self,confirmed=False,*,torque_limit=300,speed_deg_s=5,wait_for_feedback=False):
        self.require_idle()
        if confirmed is not True:
            raise CalibrationError('请确认所有零位已核对、躯干已固定且手已离开所有关节')
        if type(torque_limit) is not int or not 100<=torque_limit<=681:
            raise CalibrationError('归零扭矩上限需100—681')
        if type(speed_deg_s) not in (int,float) or not math.isfinite(speed_deg_s) or not 1<=speed_deg_s<=5:
            raise CalibrationError('归零速度需1—5°/秒')
        selected=list(IDS)
        now=self.checked_snapshot()
        m=self.mouth
        if 34 in selected and (not m or m.get('closed_raw')!=2048 or not 1024<=m.get('open_raw',0)<=2032 or m.get('eeprom_offset_raw')!=word(now['34'],31)):
            raise CalibrationError('嘴部闭合零位或配置未核验，不能全部归零')
        self.validate_zero_range(now)
        self.current=now;self.pending=None
        # Never raise a lower pre-existing cap; mouth remains at the calibrated
        # low-force inspection setting. No EEPROM or runtime config is changed.
        caps={str(s):min(torque_limit,300 if s==34 else 681,word(now[str(s)],48)) for s in selected}
        if any(v<100 for v in caps.values()):raise CalibrationError('现有扭矩上限不足100，请先检查舵机参数')
        self.zero_check=dict(phase='arming',ids=selected,armed=0,progress=0,targets={str(s):2048 for s in selected},
            max_error_deg=max(abs(word(r,56)-2048)*360/4096 for r in (now[str(s)] for s in selected)),speed_deg_s=speed_deg_s,
            torque_limits=caps,waiting_for=[])
        self._zero_feedback=wait_for_feedback;self._zero_wait_since=None
        self._zero_started=time.monotonic();self._zero_last=self._zero_started
        self._zero_distance=max(1,max(abs(word(r,56)-2048) for r in (now[str(s)] for s in selected)))
        self._zero_settled=0
        self.save('zero-start',dict(registers=now,inspection=self.zero_check))
        # Merely queue the operation. Each heartbeat performs a bounded step,
        # leaving the serial owner available to process OFF between steps.
        return self.status()

    def validate_zero_range(self,rows):
        for sid in IDS:
            raw=word(rows[str(sid)],56)
            if sid==34:
                valid=self.mouth['open_raw']-2<=raw<=self.mouth['closed_raw']+2
            else:
                angle=(2048-raw)*360/4096
                valid=LIMITS[sid][0]<=angle<=LIMITS[sid][1]
            if not valid:raise CalibrationError(f'ID{sid} 当前超出关节范围，请断力后手动放回范围内再归零')

    def zero_goal(self,sid,target):
        # RAM writes need no EEPROM delay. The full snapshot below must verify
        # the goal AND torque, including devices that suppress their write ACK.
        self.held.add(sid)
        error=self.bus.write(sid,42,int(target).to_bytes(2,'little'))
        if error not in (0,None):raise CalibrationError(f'ID{sid} 归零目标写入失败：{error}')
        if read(self.bus,sid,40,1)==[0]:write_checked(self.bus,sid,40,[1],holding=True)
        self.current[str(sid)][40]=1
        self.current[str(sid)][42:44]=list(int(target).to_bytes(2,'little'))

    def advance_zero(self):
        if not self.zero_running():return self.refresh()
        try:
            if time.monotonic()-self._zero_started>180:
                raise CalibrationError('归零超时，请检查关节是否卡住或保持扭矩不足')
            self.refresh()
            z=self.zero_check
            selected=z.get('ids',IDS)
            self.validate_zero_range(self.current)
            if z['phase']=='arming':
                sid=selected[z['armed']];row=self.current[str(sid)]
                position=word(row,56)
                for addr,data in [(48,z['torque_limits'][str(sid)].to_bytes(2,'little')),(50,[10 if sid==34 else 5]),(51,[20])]:
                    write_checked(self.bus,sid,addr,data,holding=True)
                    row[addr:addr+len(data)]=list(data)
                latest=word(read(self.bus,sid,56,2),0)
                if abs(latest-position)>2:raise CalibrationError(f'ID{sid} 正在移动，已取消归零')
                self.zero_goal(sid,latest)
                self.refresh()
                z['armed']+=1
                if z['armed']==len(selected):
                    z['phase']='moving';self._zero_last=time.monotonic()
            else:
                now=time.monotonic()
                if self._zero_feedback:
                    # CLI runs beside the UART, so it can wait for lagging joints
                    # instead of advancing an open-loop goal toward the 4° trip.
                    remaining=max(abs(word(r,42)-2048) for r in (self.current[str(s)] for s in selected))
                    threshold=12 if remaining==0 else 23
                    lagging=[sid for sid in selected if abs(word(self.current[str(sid)],56)-word(self.current[str(sid)],42))>threshold]
                    z['waiting_for']=lagging
                    if lagging:
                        if self._zero_wait_since is None:self._zero_wait_since=now
                        self._zero_last=now
                        if now-self._zero_wait_since>3:
                            raise CalibrationError('等待跟随超过3秒；'+self.following_detail(lagging[0]))
                        return self.status()
                    self._zero_wait_since=None
                # No accumulated catch-up jump after browser/network delay.
                step=min(12,int(max(0,now-self._zero_last)*z['speed_deg_s']*4096/360))
                if step:
                    for sid in selected:
                        current=word(self.current[str(sid)],42);delta=2048-current
                        if delta:self.zero_goal(sid,current+max(-step,min(step,delta)))
                    self._zero_last=now
                    self.refresh()
                remaining=max(abs(word(r,42)-2048) for r in (self.current[str(s)] for s in selected))
                z['progress']=round(100*(1-min(1,remaining/self._zero_distance)))
                settled=remaining==0 and all(abs(word(r,56)-2048)<=12 for r in (self.current[str(s)] for s in selected))
                self._zero_settled=self._zero_settled+1 if settled else 0
                if self._zero_settled>=3:
                    z['phase']='holding';z['progress']=100
                    self.save('zero-held',dict(registers=self.current,inspection=z))
            z['max_error_deg']=max(abs(word(r,56)-2048)*360/4096 for r in (self.current[str(s)] for s in selected))
            return self.status()
        except Exception as exc:
            self.zero_check['phase']='failed';self.zero_check['error']=str(exc)
            self.relax_all()
            self.save('zero-failed',dict(inspection=self.zero_check,registers=self.current))
            raise

class FakeBus:
    """Explicit UI demonstration only; motor movement is never simulated as live."""
    def __init__(self,follow_goals=False):
        self.follow_goals=follow_goals
        self.rows={};self.writes=[]
        for sid in IDS:
            r=[0]*80;r[3:7]=[10,31,sid,0];r[33]=4;r[55]=1;r[62]=74;r[63]=28
            r[48:52]=[232,3,32,40];r[56:58]=list((2048+(sid%5-2)*7).to_bytes(2,'little'))
            self.rows[sid]=r
    def read(self,sid,addr,n):return 0,bytes(self.rows[sid][addr:addr+n])
    def write(self,sid,addr,data):
        r=self.rows[sid];physical=word(r,56)+signed(word(r,31));self.writes.append((sid,addr,list(data)))
        r[addr:addr+len(data)]=list(data)
        if addr==31:r[56:58]=list(((physical-signed(word(r,31)))%4096).to_bytes(2,'little'))
        if addr==42:
            r[40]=1 # HD1910 mode 4's potentially arming goal write.
            if self.follow_goals:
                lo,hi=getattr(self,'stops',{}).get(sid,(0,4095))
                r[56:58]=list(max(lo,min(hi,int.from_bytes(data,'little'))).to_bytes(2,'little'))
        return 0
