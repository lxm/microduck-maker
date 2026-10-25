#!/usr/bin/env python3
"""Private stdio maintenance agent. Installed only when an explicit SSH session begins."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess
import sys
import termios
import time
import uuid
from core import Session, CalibrationError, IDS, snapshot, sid_value
from storage import Journal, Configs

HARDWARE=Path('/run/lxm-kissqy-hardware.json')
UNITS=('codexpad-s10','padd','updaterd','robotd-kissqy-test','robotd')
SOCKET='/run/robotd.sock'

def native_status():
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(2);s.connect(SOCKET)
        s.sendall(b'{"jsonrpc":"2.0","id":1,"method":"robot.busStatus","params":{}}\n')
        with s.makefile('rb') as f:reply=json.loads(f.readline(2*1024*1024))
    if reply.get('id')!=1 or 'result' not in reply:
        raise CalibrationError('robotd 原生状态读取失败')
    return reply['result']

def native_off_gate():
    previous=None
    for _ in range(2):
        b=native_status();n=b.get('native') or {};hd=n.get('hd1910') or {}
        joints=n.get('joints') or [];age=n.get('sample_age_ms')
        session=(n.get('telemetry') or {}).get('session');cycle=b.get('cycle')
        if (b.get('mode')!='motion' or b.get('policy_enabled') is not False
                or n.get('torque_state_confirmed') is not False or n.get('torque_enabled') is not False
                or hd.get('restore_pending') is not False or hd.get('temporary_gains_applied') is not False
                or b.get('torque_off_pending') is not False or b.get('fresh_sample') is not True
                or n.get('all_joints_fresh') is not True or b.get('all_joints_fresh') is not True
                or n.get('held_joint_ids') or n.get('stale_joint_ids')
                or type(age) not in (int,float) or not 0<=age<=400
                or len(joints)!=15 or {j.get('id') for j in joints}!=set(IDS)
                or any(j.get('feedback_fresh') is not True for j in joints)
                or not session or type(cycle) is not int or cycle<=0):
            raise CalibrationError('请先扶稳、Select 断力；需要确认 15 颗反馈正常且全部断力、参数恢复完成')
        if previous and (session!=previous[0] or cycle<=previous[1]):
            raise CalibrationError('robotd 原生反馈未更新或会话已改变')
        previous=(session,cycle)
        time.sleep(.04)
    return dict(servo_count=15,torque=False,command_enabled=False,port=b.get('port'),
                session=session,cycle=cycle,source='robot.busStatus')

def command(*args,check=True):
    return subprocess.run(args,capture_output=True,text=True,check=check,timeout=20)

def off_gate():
    if not HARDWARE.exists():return native_off_gate()
    h=json.loads(HARDWARE.read_text())
    age=time.time()-HARDWARE.stat().st_mtime
    if not 0<=age<2 or Path(f'/proc/{int(h["pid"])}/comm').read_text().strip()!='robotd':
        raise CalibrationError('robotd 状态过期或进程不符')
    if h.get('servo_count')!=15 or h.get('torque') is not False or h.get('command_enabled') is not False or h.get('all_joints_fresh') is not True or h.get('held_joint_ids') or h.get('stale_joint_ids') or h.get('servo_restore_pending') or h.get('servo_require_relax'):
        raise CalibrationError('请先扶稳、Select 断力；需要确认 15 颗反馈正常且全部断力')
    return h

def entry_gate():
    try:return off_gate()
    except FileNotFoundError:
        # A daemon that rejects a changed encoder zero never publishes hardware
        # status. Calibration must still be reachable after a power cycle.
        # This authorises no writes: stop all owners, then require a full direct
        # snapshot (identity/mode/lock/health/torque=0) before creating Session.
        if HARDWARE.exists() or Path(SOCKET).exists():raise
        return dict(status_unavailable=True,direct_snapshot_required=True)

class SerialBus:
    def __init__(self,port='/dev/serial0'):
        import serial
        self.ser=serial.Serial(port,1_000_000,timeout=.08,write_timeout=.2,exclusive=True)
        fcntl.ioctl(self.ser.fileno(),termios.TIOCEXCL)
    def close(self):
        try:fcntl.ioctl(self.ser.fileno(),termios.TIOCNXCL)
        finally:self.ser.close()
    def packet(self,sid,op,params):
        sid_value(sid)
        length=len(params)+2;payload=bytes([sid,length,op])+bytes(params)
        self.ser.reset_input_buffer();self.ser.write(b'\xff\xff'+payload+bytes([(~sum(payload))&255]))
        deadline=time.monotonic()+.25;header=b''
        while time.monotonic()<deadline:
            b=self.ser.read(1)
            if not b:raise CalibrationError(f'ID{sid} 串口超时')
            header=(header+b)[-2:]
            if header==b'\xff\xff':break
        if header!=b'\xff\xff':raise CalibrationError('帧头无效')
        h=self.ser.read(2)
        if len(h)!=2 or h[0]!=sid or not 2<=h[1]<=82:raise CalibrationError('应答 ID/长度无效')
        body=self.ser.read(h[1])
        if len(body)!=h[1] or (sum(h)+sum(body))&255!=255:raise CalibrationError('应答长度/校验失败')
        return body[0],body[1:-1]
    def read(self,sid,addr,n):
        if type(addr) is not int or type(n) is not int or not 0<=addr<80 or not 1<=n<=80-addr:raise CalibrationError('读地址越界')
        return self.packet(sid,2,bytes([addr,n]))
    def write(self,sid,addr,data):
        # Independent final wire guard. Never offer CAL, sync-write or broadcast.
        if getattr(self,'measurement_only',False) and addr not in (40,42,48,50,51):
            raise CalibrationError('限位测量禁止写 EEPROM')
        allowed=(addr in (31,42,48) and len(data)==2) or (addr in (50,51) and len(data)==1) or (addr in (40,55) and list(data) in ([0],[1]))
        if not allowed:raise CalibrationError('禁止的写寄存器')
        try:return self.packet(sid,3,bytes([addr])+bytes(data))[0]
        except CalibrationError as e:
            if '串口超时' in str(e):return None # Some devices suppress write ACK; core MUST read back.
            raise

class Maintenance:
    def __init__(self,port='/dev/serial0',eeprom_only=False):
        self.port=port;self.eeprom_only=eeprom_only
        self.session=None;self.bus=None;self.locks=[];self.bus_lock=None;self.stopped=[];self.daemon_stopped=False;self.journal=None;self.closed=False
    def enter(self):
        if self.session or self.closed:raise CalibrationError('请重新打开会话')
        import serial  # Fail dependency checks before pausing any robot service.
        if os.geteuid()!=0 or not Path(self.port).exists() or not shutil.which('fuser'):
            raise CalibrationError(f'需要 root、{self.port} 与 fuser；尚未停止服务')
        for p in [Path('/opt/robot/.calibrator.lock'),Path('/opt/robot/.cm4-update.lock')]:
            if not p.parent.exists():continue
            f=p.open('a');self.locks.append(f);fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before=entry_gate()
        if before.get('port') and Path(before['port']).resolve()!=Path(self.port).resolve():
            raise CalibrationError('选择的串口与 robotd 当前串口不符；尚未停止服务')
        active=[u for u in UNITS if command('systemctl','is-active','--quiet',u,check=False).returncode==0]
        if 'robotd' not in active or 'robotd-kissqy-test' in active:raise CalibrationError('需要唯一运行的 robotd 正常服务')
        path=Path('/opt/robot/calibration-backups')/(time.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8])
        self.journal=Journal(path)
        self.journal.save('maintenance-before',dict(hardware=before,active_units=active,links={n:str((Path('/opt/robot')/n/'current').resolve()) for n in ('daemon','models','controller')}))
        for u in active:
            if u=='robotd':continue
            self.stopped.append(u);command('systemctl','stop',u)
        entry_gate() # Catch a Start received just before input services stopped.
        self.stopped.append('robotd');self.daemon_stopped=True;command('systemctl','stop','robotd')
        for u in UNITS:
            if command('systemctl','is-active','--quiet',u,check=False).returncode==0:raise CalibrationError(f'{u} 仍运行')
        # Existing robotd is itself launched under this flock. Acquire it only
        # after stopping robotd, and release before starting robotd again.
        p=Path('/home/lxm/walk-rehearsal-20261006/stand-tool.lock')
        if p.parent.exists():
            self.bus_lock=p.open('a');fcntl.flock(self.bus_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        owners=command('fuser',self.port,check=False)
        if owners.returncode!=1 or owners.stdout.strip():raise CalibrationError('串口仍有其他使用者')
        self.bus=SerialBus(self.port);configs=Configs('/etc/robot',self.journal,eeprom_only=self.eeprom_only)
        rows=snapshot(self.bus);configs.validate(rows)
        self.session=Session(self.bus,self.journal.save,configs.commit,configs.mouth)
        return dict(**self.status(),initial_backup=dict(registers=self.session.initial,configs={k:None if v is None else v.decode() for k,v in configs.expected.items()}))
    def status(self):
        return dict(**self.session.status(),backup=str(self.journal.path),mode='hardware')
    def handle(self,req):
        action=req.get('action')
        if action=='enter':
            if req.get('supported') is not True:raise CalibrationError('先把小鸭固定在支架上，腿部留出空间')
            return self.enter()
        if not self.session:raise CalibrationError('尚未进入标定会话')
        if action=='status':
            if not (self.session.probe and self.session.probe.running()):self.session.advance_zero()
        elif action=='probe':
            self.session.start_probe(sid=req.get('id'),direction=req.get('direction'),
                travel_deg=req.get('travel_deg',10),speed_deg_s=req.get('speed_deg_s',1),
                torque_limit=req.get('torque_limit',100),repeats=req.get('repeats',3),confirmed=req.get('confirmed'))
        elif action=='zero':self.session.start_zero(req.get('confirmed'),ids=req.get('ids'),detached_output=req.get('detached_output',False),speed_deg_s=3 if req.get('ids') is not None else 5,wait_for_feedback=req.get('ids') is not None)
        elif action=='sample':self.session.sample(req.get('id'),0,req.get('endpoint'))
        elif action=='apply':self.session.apply(req.get('token'))
        elif action=='undo':self.session.undo(req.get('id'))
        elif action=='hold':
            if req.get('clear') is not True:raise CalibrationError('单独使能前请确认支架稳固、手已离开当前关节')
            self.session.hold(req.get('id'))
        elif action=='release':self.session.release(req.get('id'))
        elif action=='relax':self.session.relax_all()
        elif action=='finish':return self.close()
        else:raise CalibrationError('不支持的操作')
        return self.status()
    def tick_probe(self,last_request):
        probe=self.session.probe if self.session else None
        if not probe or not probe.running():return
        try:
            from limit_probe import HEARTBEAT_SECONDS
            if time.monotonic()-last_request>HEARTBEAT_SECONDS:
                probe.abort('页面心跳超过2秒未更新，已停止探测')
            else:self.session.advance_probe()
        except Exception:
            # advance/abort records the fault and attempts OFF. Keep stdio's
            # request/reply alignment; the next status returns the terminal result.
            pass
    def close(self):
        if self.closed:return dict(closed=True)
        safe=not self.daemon_stopped
        error=None
        try:
            if self.session:self.session.relax_all();safe=True
            elif self.bus:snapshot(self.bus);safe=True
        except Exception as exc:error=str(exc);safe=False
        finally:
            if self.bus:
                try:self.bus.close()
                except Exception as exc:error='串口关闭失败：'+str(exc);safe=False
                finally:self.bus=None
            if self.bus_lock:self.bus_lock.close();self.bus_lock=None
        try:
            if safe:
                if 'robotd' in self.stopped:
                    command('systemctl','start','robotd')
                    deadline=time.monotonic()+8
                    while True:
                        try:off_gate();break
                        except Exception:
                            if time.monotonic()>deadline:raise CalibrationError('robotd 重启后未确认全部断力，手柄服务保持暂停')
                            time.sleep(.2)
                for u in self.stopped:
                    if u!='robotd':command('systemctl','start',u)
            else:raise CalibrationError(error or '未核验串口全部断力；服务保持暂停，请托稳后切断主电源')
        except Exception as exc:error=str(exc);safe=False
        finally:
            self.closed=True
            for f in self.locks:f.close()
        result=dict(closed=True,all_torque_off=safe,services_restored=error is None,backup=str(self.journal.path) if self.journal else None,error=error)
        if self.journal:self.journal.save('closed',result)
        return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial-port',choices=('/dev/serial0','/dev/ttyS2'),default='/dev/serial0')
    parser.add_argument('--eeprom-only',action='store_true')
    parser.add_argument('--check',action='store_true',help='只读状态检查，不停止服务或打开串口')
    args=parser.parse_args()
    if args.check:
        h=off_gate()
        if h.get('port') and Path(h['port']).resolve()!=Path(args.serial_port).resolve():
            raise CalibrationError('选择的串口与 robotd 当前串口不符')
        print(json.dumps(h,ensure_ascii=False));return
    m=Maintenance(args.serial_port,args.eeprom_only)
    def stop(*_):raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGHUP,stop)
    print(json.dumps(dict(ready=True)),flush=True)
    last_request=next_probe=time.monotonic()
    try:
        while not m.closed:
            # Browser heartbeat is forwarded by the local server. SSH alive alone
            # must not keep an unattended calibration pose energised forever.
            probing=bool(m.session and m.session.probe and m.session.probe.running())
            readable,_,_=select.select([sys.stdin],[],[],.05 if probing else (12 if m.session else 60))
            if not readable and not probing:break
            if readable:
                line=sys.stdin.readline()
                if not line:break
                try:
                    req=json.loads(line);last_request=time.monotonic();result=m.handle(req)
                    print(json.dumps(dict(ok=True,result=result),ensure_ascii=False),flush=True)
                except Exception as exc:
                    extra=''
                    if m.session:
                        try:m.session.relax_all()
                        except Exception as off_error:extra='；'+str(off_error)
                    print(json.dumps(dict(ok=False,error=str(exc)+extra,result=m.status() if m.session else None),ensure_ascii=False),flush=True)
                    if not m.session:break
            if time.monotonic()>=next_probe:
                m.tick_probe(last_request)
                next_probe=time.monotonic()+.1
    finally:
        result=m.close()
        print(json.dumps(dict(closed=result),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
