#!/usr/bin/env python3
"""Logged suspended stepping inspection. Default is offline simulation, never floor walking."""
import argparse
import csv
import datetime as dt
import json
import math
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from core import CalibrationError, FakeBus, HOME, IDS, LIMITS, Session, read, word, write_checked

LEGS = [10, 11, 12, 13, 14, 20, 21, 22, 23, 24]
CPD = 4096 / 360
CSV_FIELDS = ['elapsed_s', 'phase', 'id', 'enabled', 'target_raw', 'actual_raw',
              'target_deg', 'actual_deg', 'error_deg', 'volts', 'temperature_c',
              'p', 'd', 'limit_raw', 'load_raw', 'current_raw', 'sample_ms']


def raw(angle):
    return round(2048 - angle * CPD)


def poses(lift):
    neutral = {sid: raw(HOME[sid]) for sid in LEGS}
    result = []
    for side, hip, knee, ankle, sign in [('right', 12, 13, 14, -1), ('left', 22, 23, 24, 1)]:
        degrees = {sid: HOME[sid] for sid in LEGS}
        # Mirrored joint axes from robot_walk.xml: hip - knee + ankle = 0.
        # This folds the leg while retaining the model's foot pitch: in-place stepping.
        degrees[hip] += sign * lift * 2 / 3
        degrees[knee] += sign * lift
        degrees[ankle] += sign * lift / 3
        result.extend([(side + '_lift', {sid: raw(a) for sid, a in degrees.items()}),
                       (side + '_lower', dict(neutral))])
    for _, targets in result:
        for sid, value in targets.items():
            angle = (2048 - value) / CPD
            if not 0 <= value <= 4095 or not LIMITS[sid][0] <= angle <= LIMITS[sid][1]:
                raise CalibrationError(f'ID{sid}: planned pose outside assembly range')
    return neutral, result


class StopRequest:
    """First cancellation reason; no blocking lock in Python signal handlers."""
    def __init__(self): self.reasons = []
    def request(self, reason):
        if not self.reasons: self.reasons.append(str(reason))
    def is_set(self): return bool(self.reasons)
    @property
    def reason(self): return self.reasons[0] if self.reasons else None
    def wait(self, seconds):
        time.sleep(seconds)
        return self.is_set()


def prepare_keyboard(stream, flush=None):
    if not stream.isatty(): return False
    try:
        if flush is None:
            import termios
            termios.tcflush(stream.fileno(), termios.TCIFLUSH)
        else: flush(stream)
    except (ImportError, OSError, ValueError, AttributeError): pass
    return True


def keyboard_loop(stream, stop, *, enter_stops=True, eof_stops=False):
    """Only a newly entered line requests STOP. EOF is not an Enter key."""
    while not stop.is_set():
        try: line = stream.readline()
        except (OSError, ValueError):
            if eof_stops:stop.request('terminal:input closed')
            return
        if line == '':
            if eof_stops:stop.request('terminal:EOF')
            return
        if enter_stops and line.endswith(('\n', '\r')):
            stop.request('keyboard:Enter')
            return


class Clock:
    def __init__(self, virtual=False): self.virtual, self.t = virtual, 0.0
    def now(self): return self.t if self.virtual else time.monotonic()
    def sleep(self, seconds):
        if self.virtual: self.t += seconds
        else: time.sleep(seconds)


class DemoMaintenance:
    def __init__(self):
        self.bus = FakeBus(follow_goals=True)
        for sid in IDS: self.bus.rows[sid][56:58] = list(raw(HOME[sid] if sid in LEGS else 0).to_bytes(2, 'little'))
        self.journal = type('DemoJournal', (), {'path': 'DEMO-no-robot', 'save': lambda *_: None})()
    def enter(self): self.session = Session(self.bus, lambda *_: None, lambda *_: None)
    def close(self):
        if hasattr(self, 'session'): self.session.relax_all()
        return dict(all_torque_off=True, services_restored=True, mode='demo')


class Monitor:
    """Read actual registers, without padding/fabricating an 80-byte snapshot.

    RAM40..70 includes enable/goal/limit/PDI/lock and all native feedback.
    Identity, mode, EEPROM zero and stored gains (3..39) are also compared at
    least once per second. Every failed transaction stops the inspection.
    """
    def __init__(self, session, clock, emit, minimum_v):
        self.s, self.clock, self.emit, self.minimum_v = session, clock, emit, minimum_v
        self.start = clock.now(); self.identity_at = None; self.last_complete = None
        self.phase = 'preflight'
    def sample(self, identity=False):
        begin = self.clock.now()
        if self.last_complete is not None and begin - self.last_complete > .75:
            raise CalibrationError('feedback loop gap >750ms; motion cancelled')
        identity = identity or self.identity_at is None or begin - self.identity_at >= 1
        pending = {}
        for sid in IDS:
            expected = self.s.current[str(sid)]
            if identity:
                actual = read(self.s.bus, sid, 3, 37)
                if actual != self.s.initial[str(sid)][3:40]:
                    raise CalibrationError(f'ID{sid}: identity/mode/EEPROM changed')
            try: data = read(self.s.bus, sid, 40, 31)
            except Exception as exc:
                self.emit(dict(event='read_error',phase=self.phase,id=sid,address=40,length=31,error=str(exc)))
                raise
            if data[:16] != expected[40:56]:
                raise CalibrationError(f'ID{sid}: unexpected enable/goal/parameter/lock change')
            if data[0] != int(sid in self.s.held) or data[15] != 1:
                raise CalibrationError(f'ID{sid}: torque/lock mismatch')
            position = word(data, 16)
            if position > 4095 or data[25] or data[23] >= 48:
                raise CalibrationError(f'ID{sid}: encoder/fault/temperature check failed')
            volts = data[22] / 10
            if not self.minimum_v <= volts <= 8.4:
                self.emit(dict(event='rejected_sample',phase=self.phase,id=sid,volts=volts,raw_40_70=data,reason='voltage'))
                raise CalibrationError(f'ID{sid}: voltage {volts:.1f}V outside {self.minimum_v:.1f}–8.4V')
            if sid in LEGS and not LIMITS[sid][0] <= (2048-position)/CPD <= LIMITS[sid][1]:
                raise CalibrationError(f'ID{sid}: measured joint outside assembly range')
            if sid in self.s.held and abs(position - word(data, 2)) > 46:
                raise CalibrationError(f'ID{sid}: following error >4 degrees')
            pending[sid] = data
        end = self.clock.now()
        if end - begin > .5: raise CalibrationError('complete feedback scan >500ms')
        if identity: self.identity_at = end
        self.last_complete = end
        rows = []
        for sid, data in pending.items():
            self.s.current[str(sid)][40:71] = data
            if sid not in LEGS: continue
            position, goal = word(data, 16), word(data, 2)
            rows.append(dict(elapsed_s=round(end-self.start, 4), phase=self.phase, id=sid,
                enabled=sid in self.s.held, target_raw=goal, actual_raw=position,
                target_deg=round((2048-goal)/CPD, 4), actual_deg=round((2048-position)/CPD, 4),
                error_deg=round((position-goal)/CPD, 4), volts=data[22]/10,
                temperature_c=data[23], p=data[10], d=data[11], limit_raw=word(data, 8),
                load_raw=word(data, 20), current_raw=word(data, 29), sample_ms=round((end-begin)*1000, 2)))
        self.emit(dict(event='sample', phase=self.phase, rows=rows))
        return rows


def inspect(maintenance, stopped, emit, *, lift=12, cycles=2, speed=3, cap=300,
            minimum_v=6.5, countdown=5, clock=None):
    clock = clock or Clock(); start = clock.now(); entered = False; entry_attempted = False; failure = None; cleanup = None
    sink = emit; logging_error = [None]
    def emit(event):
        # A broken SSH stdout or full local log must never skip actuator cleanup.
        try: sink(event)
        except Exception as exc: logging_error[0] = str(exc) or type(exc).__name__
    def check():
        if logging_error[0]: raise CalibrationError('logging/output failed: '+logging_error[0])
        stop_reason=stopped()
        if stop_reason: raise CalibrationError(stop_reason if isinstance(stop_reason,str) else 'operator stop / controller heartbeat lost')
        if clock.now()-start > 180: raise CalibrationError('inspection exceeds 180 seconds')
    try:
        check(); entry_attempted = True; maintenance.enter(); entered = True; s = maintenance.session
        m = Monitor(s, clock, emit, minimum_v)
        neutral, full = poses(lift); _, half = poses(lift/2)
        for sid in LEGS:
            if word(s.current[str(sid)], 48) < 100: raise CalibrationError(f'ID{sid}: initial limit below 100')
        # Five complete all-ID checks, with NO actuator activation.
        for _ in range(5): check(); m.sample(identity=True); clock.sleep(.05)
        emit(dict(event='ready', backup=str(maintenance.journal.path), lift_deg=lift,
                  cycles=cycles, speed_deg_s=speed, limit_max_raw=cap,
                  pd='preserve original per-servo P/D/I', countdown_s=countdown))
        def dwell(seconds, phase):
            m.phase=phase; until=clock.now()+seconds
            while clock.now()<until: check(); m.sample(); clock.sleep(.05)
        dwell(countdown, 'countdown')
        def command_goal(sid, value, preload=False):
            check()
            if sid not in LEGS or type(value) is not int or not 0<=value<=4095:
                raise CalibrationError('invalid individual leg goal')
            if not LIMITS[sid][0]<=(2048-value)/CPD<=LIMITS[sid][1]:
                raise CalibrationError(f'ID{sid}: goal outside assembly range')
            if not preload and abs(value-word(s.current[str(sid)],42))>4:
                raise CalibrationError(f'ID{sid}: goal increment exceeds 4 counts')
            s.held.add(sid)  # Mode 4 goal writes can arm, including a lost ACK.
            error=s.bus.write(sid,42,value.to_bytes(2,'little'))
            if error not in (0,None):raise CalibrationError(f'ID{sid}: goal write error {error}')
            actual=read(s.bus,sid,40,4)
            if word(actual,2)!=value:raise CalibrationError(f'ID{sid}: goal readback mismatch; enable refused')
            if actual[0]==0:write_checked(s.bus,sid,40,[1],holding=True)
            elif actual[0]!=1:raise CalibrationError(f'ID{sid}: invalid torque state')
            s.current[str(sid)][40]=1;s.current[str(sid)][42:44]=list(value.to_bytes(2,'little'))
        for sid in LEGS:
            check(); m.phase=f'arm_ID{sid}'; m.sample()
            r=s.current[str(sid)]; position=word(r,56); limit=min(cap, word(r,48))
            # Preserve original PID. Bounded output first, then fresh-position preload.
            write_checked(s.bus,sid,48,limit.to_bytes(2,'little'),holding=True)
            r[48:50]=list(limit.to_bytes(2,'little'))
            positions=[word(read(s.bus,sid,56,2),0) for _ in range(3)]
            if max(abs(p-position) for p in positions)>2:
                raise CalibrationError(f'ID{sid}: moving before enable')
            check(); command_goal(sid,positions[-1],preload=True); m.sample()
        def move(targets, label):
            m.phase=label; emit(dict(event='phase_start',phase=label,targets=targets))
            phase_start=last=clock.now(); waiting=None; settling=None; stable=0
            while True:
                check(); rows=m.sample(); now=clock.now()
                if now-phase_start>35: raise CalibrationError(f'{label}: phase timeout')
                lag=[r['id'] for r in rows if r['enabled'] and abs(r['actual_raw']-r['target_raw'])>23]
                if lag:
                    if waiting is None: waiting=now
                    if now-waiting>3: raise CalibrationError(f'{label}: lag >2 degrees for 3s, IDs {lag}')
                    last=now
                else:
                    waiting=None
                    goals={sid:word(s.current[str(sid)],42) for sid in LEGS}
                    remaining=max(abs(targets[sid]-goals[sid]) for sid in LEGS)
                    step=min(4,int(max(0,now-last)*speed*CPD))
                    if remaining and step:
                        ratio=min(1,step/remaining)
                        for sid in LEGS:
                            goal=goals[sid]+round((targets[sid]-goals[sid])*ratio)
                            if goal!=goals[sid]: check(); command_goal(sid,goal)
                        last=now
                    if not remaining:
                        if settling is None: settling=now
                        at_pose=all(abs(r['actual_raw']-targets[r['id']])<=12 for r in rows)
                        stable=stable+1 if at_pose else 0
                        if stable>=3: break
                        if now-settling>3: raise CalibrationError(f'{label}: not settled within 1 degree')
                clock.sleep(.05)
            emit(dict(event='phase_done',phase=label,seconds=round(clock.now()-phase_start,3)))
            dwell(.5,label+'_observe')
        move(neutral,'prepare_HOME')
        for stage, count, trajectory in [('half',1,half),('full',cycles,full)]:
            for cycle in range(1,count+1):
                for label,target in trajectory: move(target,f'{stage}_{cycle}_{label}')
        dwell(2,'finished_HOME')
    except Exception as exc:
        failure=str(exc); emit(dict(event='failed',error=failure))
    finally:
        if entry_attempted:
            emit(dict(event='cleanup_start',message='all torque off, restore original RAM parameters and services'))
            had_uart=bool(entered or getattr(maintenance,'bus',None) is not None)
            try: cleanup=maintenance.close()
            except Exception as exc: cleanup=dict(all_torque_off=False,services_restored=False,error=str(exc))
            if not had_uart:
                cleanup=dict(cleanup,all_torque_off=None,torque_status='not inspected')
        else:
            emit(dict(event='entry_cancelled',message='Cancelled before maintenance entry; no robot operations; torque not inspected'))
            cleanup=dict(attempted=False,all_torque_off=None,services_restored=None,backup=None,
                         torque_status='not inspected; maintenance entry was not attempted')
        if logging_error[0] and not failure: failure='logging/output failed: '+logging_error[0]
        result=dict(event='finished',mode='demo' if isinstance(maintenance,DemoMaintenance) else 'hardware',
                    entered=entered,entry_attempted=entry_attempted,error=failure,cleanup=cleanup,
                    torque_off_verified=bool(entered and cleanup.get('all_torque_off')),
                    elapsed_s=round(clock.now()-start,3))
        emit(result)
        if logging_error[0] and not result['error']: result['error']='logging/output failed: '+logging_error[0]
    return result


class Logs:
    def __init__(self,path,mode):
        path.mkdir(parents=True,exist_ok=False); self.path=path; self.mode=mode; self.final=None
        self.events=(path/'events.jsonl').open('w'); self.text=(path/'run.log').open('w')
        self.csvfile=(path/'samples.csv').open('w',newline=''); self.csv=csv.DictWriter(self.csvfile,fieldnames=CSV_FIELDS);self.csv.writeheader()
        self.stats={}; self.last_status=-1
    def line(self,text):
        line=f'{dt.datetime.now().astimezone().isoformat(timespec="seconds")} [{self.mode}] {text}'
        print(line,flush=True); self.text.write(line+'\n');self.text.flush()
    def emit(self,event):
        self.events.write(json.dumps({'received_unix':time.time(),**event},ensure_ascii=False)+'\n');self.events.flush()
        if event.get('event')=='sample':
            for row in event['rows']:
                self.csv.writerow(row)
                if not row['enabled']: continue
                st=self.stats.setdefault(str(row['id']),dict(samples=0,max_error_deg=0,min_v=99,max_temp_c=0))
                st['samples']+=1; st['max_error_deg']=max(st['max_error_deg'],abs(row['error_deg']))
                st['min_v']=min(st['min_v'],row['volts']);st['max_temp_c']=max(st['max_temp_c'],row['temperature_c'])
            self.csvfile.flush()
            t=event['rows'][0]['elapsed_s']
            if t-self.last_status>=1:
                self.last_status=t; active=[r for r in event['rows'] if r['enabled']]
                name=event['phase']
                for english,chinese in [('prepare_HOME','准备参考姿态'),('right_lift','右腿抬起'),('right_lower','右腿放下'),('left_lift','左腿抬起'),('left_lower','左腿放下'),('half','半幅'),('full','全幅'),('countdown','开始倒计时'),('observe','观察停留'),('finished_HOME','结束参考姿态')]:name=name.replace(english,chinese)
                self.line(f"{name} | t={t:.1f}s | enabled={len(active)}/10 | "
                          f"max_error={max((abs(r['error_deg']) for r in active),default=0):.2f}deg | "
                          f"min_voltage={min(r['volts'] for r in event['rows']):.1f}V")
        elif event.get('event')=='finished':
            self.final=event; summary={**event,'per_joint':self.stats,'logs':str(self.path.resolve())}
            (self.path/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
            self.line(json.dumps(event,ensure_ascii=False))
        else: self.line(json.dumps(event,ensure_ascii=False))
    def close(self):
        for f in (self.events,self.text,self.csvfile): f.close()


def worker(args):
    import agent
    stop=StopRequest();last=[None];received=threading.Event()
    def heartbeat():
        for line in sys.stdin:
            message=line.strip()
            if message=='STOP': stop.request('controller:STOP');received.set();return
            if message!='HEARTBEAT':stop.request('controller:invalid heartbeat message');received.set();return
            last[0]=time.monotonic();received.set()
        stop.request('controller:stdin closed');received.set()
    threading.Thread(target=heartbeat,daemon=True).start()
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
        signal.signal(sig,lambda signum,_:stop.request('remote_signal:'+signal.Signals(signum).name))
    received.wait(6)
    def reason():
        if stop.is_set():return stop.reason
        if last[0] is None:return 'controller:no startup heartbeat received'
        if time.monotonic()-last[0]>2:return 'controller:heartbeat timeout >2s'
        return False
    result=inspect(agent.Maintenance(args.serial_port,eeprom_only=True),reason,
        lambda e:print(json.dumps(e,ensure_ascii=False),flush=True),lift=args.lift_deg,cycles=args.cycles,
        speed=args.speed,cap=args.torque_limit,minimum_v=args.min_voltage,countdown=args.countdown)
    return 0 if not result['error'] and result['torque_off_verified'] and result['cleanup'].get('services_restored') else 1


def validate_onboard(stream, effective_uid):
    if not stream.isatty():raise CalibrationError('本机实机模式需要交互终端；远程登录请用 ssh -t，不能重定向标准输入')
    if effective_uid!=0:raise CalibrationError('请在 Zero 上使用 sudo python3 ... --onboard --supported')


def log_root(onboard=False):
    home=Path.home()
    if onboard and os.environ.get('SUDO_USER'):
        import pwd
        home=Path(pwd.getpwnam(os.environ['SUDO_USER']).pw_dir)
    return home/'.microduck-gait-tests'


def onboard(args,logs):
    import agent
    stop=StopRequest()
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
        signal.signal(sig,lambda signum,_:stop.request('onboard_signal:'+signal.Signals(signum).name))
    prepare_keyboard(sys.stdin)
    threading.Thread(target=keyboard_loop,args=(sys.stdin,stop),
        kwargs=dict(enter_stops=False,eof_stops=True),daemon=True).start()
    logs.emit(dict(event='onboard_controls_ready',keyboard_enter=False,ctrl_c_stop=True,
                   terminal_close_stop=True,pc_heartbeat_required=False))
    logs.line('Zero 本机直跑：Ctrl+C 停止；普通回车忽略；终端关闭或 SIGHUP 会请求断力退出。')
    result=inspect(agent.Maintenance(args.serial_port,eeprom_only=True),
        lambda:stop.reason if stop.is_set() else False,logs.emit,lift=args.lift_deg,
        cycles=args.cycles,speed=args.speed,cap=args.torque_limit,minimum_v=args.min_voltage,
        countdown=args.countdown)
    return 0 if not result['error'] and result['torque_off_verified'] and result['cleanup'].get('services_restored') else 1


def remote(args,logs):
    stop=StopRequest()
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
        signal.signal(sig,lambda signum,_:stop.request('local_signal:'+signal.Signals(signum).name))
    opts=['-o','BatchMode=yes','-o','ConnectTimeout=6','-o','ServerAliveInterval=3','-o','ServerAliveCountMax=3']
    dest='/tmp/duck-gait-test-'+uuid.uuid4().hex[:16]; here=Path(__file__).resolve().parent
    proc=None; code=1; stderr_file=(logs.path/'stderr.log').open('w'); messages=queue.Queue()
    try:
        keyboard_enabled=prepare_keyboard(sys.stdin)
        if keyboard_enabled:threading.Thread(target=keyboard_loop,args=(sys.stdin,stop),daemon=True).start()
        logs.emit(dict(event='stop_controls_ready',keyboard_enter=keyboard_enabled,signals=['SIGINT','SIGTERM','SIGHUP'],pending_tty_input_discarded=keyboard_enabled))
        logs.line('Uploading temporary maintenance program; Enter or Ctrl+C requests STOP.')
        subprocess.run(['ssh',*opts,args.ssh,'mkdir','-m','700',dest],check=True,timeout=25,stdin=subprocess.DEVNULL)
        subprocess.run(['scp',*opts,*[str(here/n) for n in ('gait_test.py','core.py','storage.py','agent.py','board_setup.py')],args.ssh+':'+dest+'/'],check=True,timeout=40,stdin=subprocess.DEVNULL)
        if stop.is_set(): raise CalibrationError('cancelled before robot access: '+str(stop.reason))
        flags=['--worker','--supported','--serial-port',args.serial_port,'--lift-deg',str(args.lift_deg),
               '--cycles',str(args.cycles),'--speed',str(args.speed),'--torque-limit',str(args.torque_limit),
               '--min-voltage',str(args.min_voltage),'--countdown',str(args.countdown)]
        proc=subprocess.Popen(['ssh','-T',*opts,args.ssh,'sudo','-n','python3','-u',dest+'/gait_test.py',*flags],
             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
        def feed():
            try:
                while not stop.is_set() and proc.poll() is None:
                    proc.stdin.write('HEARTBEAT\n');proc.stdin.flush();stop.wait(.3)
                if proc.poll() is None:proc.stdin.write('STOP\n');proc.stdin.flush()
            except (BrokenPipeError,OSError,ValueError) as exc:
                if proc.poll() is None:messages.put(('heartbeat_send_failed',str(exc)))
        def output():
            for line in proc.stdout:messages.put(line)
            messages.put(None)
        def errors():
            for line in proc.stderr:stderr_file.write(line);stderr_file.flush();logs.line('REMOTE STDERR: '+line.rstrip())
        threading.Thread(target=feed,daemon=True).start();threading.Thread(target=output,daemon=True).start()
        error_thread=threading.Thread(target=errors,daemon=True);error_thread.start()
        started=time.monotonic();cancel_at=None
        while True:
            if time.monotonic()-started>220: stop.request('local_timeout:220s')
            if stop.is_set() and cancel_at is None:
                cancel_at=time.monotonic();logs.emit(dict(event='stop_requested',reason=stop.reason));logs.line('STOP requested ('+str(stop.reason)+'); waiting for remote cleanup result.')
            if cancel_at and time.monotonic()-cancel_at>40:
                raise CalibrationError('remote cleanup acknowledgement unavailable; torque state UNKNOWN')
            try: line=messages.get(timeout=.2)
            except queue.Empty: continue
            if line is None: break
            if isinstance(line,tuple):
                logs.emit(dict(event=line[0],error=line[1]));stop.request(line[0]);continue
            try: logs.emit(json.loads(line))
            except json.JSONDecodeError: raise CalibrationError('invalid maintenance reply; outcome unknown')
        code=proc.wait(timeout=10);error_thread.join(timeout=2)
        if logs.final is None: raise CalibrationError('connection ended without cleanup result; torque state UNKNOWN')
    except Exception as exc:
        stop.request('transport_failure:'+str(exc));logs.emit(dict(event='transport_failed',error=str(exc),stop_reason=stop.reason))
        if logs.final is None:
            (logs.path/'summary.json').write_text(json.dumps(dict(mode='hardware',error=str(exc),
                 torque_off_verified=False,cleanup_unknown=proc is not None,per_joint=logs.stats),ensure_ascii=False,indent=2))
    finally:
        stop.request('local_shutdown')
        if proc and proc.stdin:
            try: proc.stdin.close()
            except OSError: pass
        stderr_file.close()
    return code if logs.final else 1


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group();g.add_argument('--demo',action='store_true');g.add_argument('--ssh');g.add_argument('--onboard',action='store_true',help='在 Zero 本机直接运行；需要 sudo 和交互终端，Ctrl+C 停止')
    p.add_argument('--plan',action='store_true');p.add_argument('--supported',action='store_true',help='trunk fixed, legs suspended, hands clear, Select torque-off')
    p.add_argument('--serial-port',choices=['/dev/serial0','/dev/ttyS2'],default='/dev/ttyS2')
    p.add_argument('--lift-deg',type=float,default=12);p.add_argument('--cycles',type=int,default=2)
    p.add_argument('--speed',type=float,default=3);p.add_argument('--torque-limit',type=int,default=300)
    p.add_argument('--min-voltage',type=float,default=6.5);p.add_argument('--countdown',type=int,default=5)
    p.add_argument('--log-dir',type=Path);p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args(argv)
    if not math.isfinite(a.lift_deg) or not 4<=a.lift_deg<=15:p.error('lift-deg must be 4..15')
    if not 1<=a.cycles<=3:p.error('cycles must be 1..3')
    if not math.isfinite(a.speed) or not 1<=a.speed<=5:p.error('speed must be 1..5 degrees/s')
    if not 100<=a.torque_limit<=300:p.error('torque-limit must be 100..300; never raises a lower initial limit')
    if not math.isfinite(a.min_voltage) or not 6.3<=a.min_voltage<=7.2:p.error('min-voltage must be 6.3..7.2')
    if not 3<=a.countdown<=15:p.error('countdown must be 3..15')
    if a.worker and (a.demo or a.ssh or a.plan or a.onboard):p.error('internal worker cannot be combined with demo, ssh, onboard or plan')
    if a.ssh and not re.fullmatch(r'[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]*',a.ssh):p.error('ssh must be user@hostname')
    if (a.ssh or a.worker or a.onboard) and not a.supported and not a.plan:p.error('hardware requires --supported after physical preparation')
    return a


def main(argv=None):
    args=parse(argv);neutral,full=poses(args.lift_deg)
    plan=dict(kind='suspended in-place stepping; no balance policy',legs=LEGS,
        warmup_lift_deg=args.lift_deg/2,full_lift_deg=args.lift_deg,full_cycles=args.cycles,
        speed_deg_s=args.speed,output_limit_max_raw=args.torque_limit,min_voltage_v=args.min_voltage,
        neutral_deg={sid:HOME[sid] for sid in LEGS},
        keyframes=[dict(phase=n,degrees={sid:round((2048-v)/CPD,3) for sid,v in t.items()}) for n,t in full])
    if args.plan: print(json.dumps(plan,ensure_ascii=False,indent=2));return 0
    if args.worker:return worker(args)
    if args.onboard:
        try:validate_onboard(sys.stdin,os.geteuid())
        except CalibrationError as exc:print(str(exc),file=sys.stderr);return 2
    path=args.log_dir or log_root(args.onboard)/(dt.datetime.now().strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8])
    logs=Logs(path,'hardware' if args.ssh or args.onboard else 'demo');(path/'plan.json').write_text(json.dumps(plan,indent=2))
    try:
        logs.line('LOG DIRECTORY: '+str(path.resolve()))
        if args.ssh:return remote(args,logs)
        if args.onboard:return onboard(args,logs)
        logs.line('OFFLINE DEMO: virtual time, simulated servos; no hardware connection.')
        result=inspect(DemoMaintenance(),lambda:False,logs.emit,lift=args.lift_deg,cycles=args.cycles,
            speed=args.speed,cap=args.torque_limit,minimum_v=args.min_voltage,countdown=args.countdown,clock=Clock(virtual=True))
        return 0 if not result['error'] else 1
    finally:logs.close()

if __name__=='__main__':sys.exit(main())
