#!/usr/bin/env python3
"""On-board HD1910 zero inspection. Run with sudo; --check never opens the UART."""
import argparse
import json
from pathlib import Path
import select
import signal
import sys
import tempfile
import time

from core import FakeBus, IDS, Session
from storage import Journal


class DemoMaintenance:
    def __init__(self):
        self.session=None
        self.journal=Journal(Path(tempfile.mkdtemp(prefix='duck-zero-demo-'))/'session')

    def enter(self):
        bus=FakeBus(follow_goals=True)
        for sid in IDS:
            bus.rows[sid][56:58]=list((2000 if sid==34 else 2148).to_bytes(2,'little'))
        mouth=dict(schema=1,id=34,mode=4,eeprom_offset_raw=0,closed_raw=2048,open_raw=1700)
        self.session=Session(bus,self.journal.save,lambda *_:None,mouth)

    def close(self):
        if self.session:self.session.relax_all()
        return dict(all_torque_off=True,services_restored=True,backup=str(self.journal.path))


def run_inspection(maintenance,stop_requested,*,torque_limit=681,speed=3,hold_seconds=120,output=print):
    """The same cleanup path handles completion, operator stop and failures."""
    result=None;failure=None;reason='stopped';entered=False
    if stop_requested():return 130
    try:
        maintenance.enter()
        entered=True
        s=maintenance.session
        output('备份：'+str(maintenance.journal.path))
        if not stop_requested():
            s.start_zero(True,torque_limit=torque_limit,speed_deg_s=speed,wait_for_feedback=True)
            output('身体 P5/D20，嘴 P10/D20；本次扭矩上限：'+json.dumps(s.zero_check['torque_limits']))
        held_since=None;last_report=-1
        while not stop_requested():
            status=s.advance_zero()
            now=time.monotonic();z=status['zero_check']
            if z['phase']=='holding' and held_since is None:
                held_since=now
                output('15 颗目标均为 2048，已到位保持。实际角度：')
                output('  '.join(f"ID{r['id']}={r['angle_deg']:+.2f}°" for r in status['servos']))
                output('保持期间按回车或 Ctrl+C 断力并恢复服务。')
            if now-last_report>=1:
                pending=z.get('waiting_for')
                text=(f"使能 {z['armed']}/15，归零 {z['progress']}%，"
                    f"距零位最大偏差 {z['max_error_deg']:.2f}°")
                if pending:text+='；暂停目标，等待跟随 ID '+','.join(map(str,pending))
                if held_since is not None:text+=f'；最多继续保持 {max(0,int(hold_seconds-(now-held_since)))} 秒'
                output(text);last_report=now
                s.save('cli-feedback',dict(inspection=z,registers=s.current))
            if held_since is not None and now-held_since>=hold_seconds:
                reason='completed';break
            time.sleep(.1)
    except (Exception,KeyboardInterrupt) as exc:
        failure=str(exc) or '用户中断'
        output('归零已停止：'+failure)
    finally:
        # A second Ctrl+C must not interrupt OFF or the service restoration gate.
        try:result=maintenance.close()
        except Exception as exc:result=dict(all_torque_off=False,services_restored=False,error=str(exc))
        if not entered:
            output('尚未开始归零；维护清理'+('已完成。' if result.get('services_restored') else '未完成：'+str(result.get('error'))))
        elif result.get('all_torque_off') and result.get('services_restored'):
            output('已核验全部断力，原服务已恢复。'+('（模拟）' if isinstance(maintenance,DemoMaintenance) else ''))
        else:
            output('未能确认断力或服务恢复：'+str(result.get('error'))+'；保持托稳，必要时切断主电源。')
    if not result.get('all_torque_off') or not result.get('services_restored'):return 2
    return 1 if failure else (0 if reason=='completed' else 130)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check',action='store_true',help='只读检查 robotd 全断力状态，不停止服务、不打开串口')
    p.add_argument('--supported',action='store_true',help='确认躯干固定、双腿悬空、手已离开、零位已核对')
    p.add_argument('--torque-limit',type=int,choices=(300,681),default=681,help='身体临时上限，默认681；不会超过进入前原值；嘴最多300')
    p.add_argument('--speed',type=int,choices=(1,2,3,4,5),default=3,help='归零速度，默认3°/秒')
    p.add_argument('--hold-seconds',type=float,default=120,help='到位后最多保持秒数，默认120，上限300')
    p.add_argument('--demo',action='store_true',help='仅模拟，无硬件或服务操作')
    args=p.parse_args(argv)
    if not 0<args.hold_seconds<=300:p.error('--hold-seconds 必须大于0且不超过300')
    if args.check:
        if args.demo:p.error('--check 用于真实 robotd 状态')
        from agent import off_gate
        try:
            h=off_gate()
            print(f"只读检查通过：{h['servo_count']} 颗反馈正常、全部断力；未停止任何服务。")
            return 0
        except Exception as exc:
            print('只读检查未通过：'+str(exc),file=sys.stderr);return 1
    if not args.demo and not sys.stdin.isatty():
        p.error('实机操作需要交互终端；远程请用 ssh -t。不要后台运行或重定向标准输入。')
    print(('模拟：' if args.demo else '实机：')+
        f'全部15颗缓慢回舵机2048并保持，身体P5/D20，上限最多{args.torque_limit}；嘴P10/D20，上限最多300。')
    print('只读现有零位，不写 EEPROM、不做 HOME、不启动模型。请先结束网页标定会话。')
    print('躯干需固定在支架上，双腿悬空，头颈嘴有空间，手离开关节。手柄将暂停。')
    if not args.supported:
        try:answer=input('已确认准备好请输入 ZERO 开始；其他输入退出：')
        except (EOFError,KeyboardInterrupt):return 130
        if answer.strip()!='ZERO':return 130
    stopped=False
    def on_signal(*_):
        nonlocal stopped
        stopped=True
    signals=(signal.SIGINT,signal.SIGTERM,signal.SIGHUP)
    previous={sig:signal.signal(sig,on_signal) for sig in signals}
    def should_stop():
        nonlocal stopped
        if not stopped and sys.stdin.isatty():
            ready,_,_=select.select([sys.stdin],[],[],0)
            if ready:
                sys.stdin.readline();stopped=True
        return stopped
    try:
        if args.demo:m=DemoMaintenance()
        else:
            from agent import Maintenance
            m=Maintenance()
        print('运行中按回车或 Ctrl+C 停止；关闭 SSH 也会请求断力。',flush=True)
        return run_inspection(m,should_stop,torque_limit=args.torque_limit,speed=args.speed,hold_seconds=args.hold_seconds,
            output=lambda s:print(s,flush=True))
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)


if __name__=='__main__':sys.exit(main())
