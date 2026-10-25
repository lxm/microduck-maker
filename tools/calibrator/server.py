#!/usr/bin/env python3
"""Local-only calibration UI; simulation by default, explicit --ssh for the duck."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import queue
import re
import secrets
import shlex
import signal
import subprocess
import threading
import time
from urllib.parse import urlsplit, unquote
import uuid
from core import CalibrationError, FakeBus, Session
from storage import Journal
from board_setup import port_argument, select_port

HERE=Path(__file__).resolve().parent

class SetupLog:
    """Bounded, independently locked log: HTTP reads work during SSH preparation."""
    def __init__(self):
        self.lock=threading.Lock();self.run=0;self.running=False;self.lines=[]
    def begin(self):
        with self.lock:
            self.run+=1;self.running=True;self.lines=[]
    def append(self,message):
        with self.lock:
            self.lines.append(time.strftime('%H:%M:%S')+'  '+str(message)[:2000])
            self.lines=self.lines[-300:]
    def finish(self):
        with self.lock:self.running=False
    def snapshot(self):
        with self.lock:return dict(run=self.run,running=self.running,lines=list(self.lines))

class Demo:
    def __init__(self,directory):
        self.journal=Journal(directory);self.session=None;self.closed=False;self.bus=FakeBus(follow_goals=True)
        self.bus.rows[34][56:58]=list((2048).to_bytes(2,'little'))
    def call(self,req):
        action=req.get('action')
        if action=='probe':raise CalibrationError('自动找限位已移除，请刷新页面')
        if action=='zero' and ('ids' in req or 'detached_output' in req):
            raise CalibrationError('单独舵机对中已移除，请使用整体零位检查')
        if self.closed:raise CalibrationError('会话已结束，请刷新页面创建新会话')
        if action=='enter':
            if self.session:raise CalibrationError('会话已经开始')
            if req.get('supported') is not True:raise CalibrationError('请确认已放到支架上')
            self.session=Session(self.bus,self.journal.save,lambda *_:None,dict(schema=1,id=34,mode=4,eeprom_offset_raw=0,closed_raw=2048,open_raw=1700))
        elif not self.session:raise CalibrationError('请先开始模拟会话')
        elif action=='status':self.session.advance_zero()
        elif action=='zero':self.session.start_zero(req.get('confirmed'))
        elif action=='sample':
            if req.get('id')==34 and 34 not in self.session.held:
                # Only demo has artificial hand positioning. No hardware endpoint
                # or generic register-write API exists in the HTTP server.
                raw=2048 if req.get('endpoint')=='closed' else 1700
                self.bus.rows[34][56:58]=list(raw.to_bytes(2,'little'))
            self.session.sample(req.get('id'),0,req.get('endpoint'))
        elif action=='apply':self.session.apply(req.get('token'))
        elif action=='undo':self.session.undo(req.get('id'))
        elif action=='hold':
            if req.get('clear') is not True:raise CalibrationError('请确认手已离开当前关节')
            self.session.hold(req.get('id'))
        elif action=='release':self.session.release(req.get('id'))
        elif action=='relax':self.session.relax_all()
        elif action=='finish':return self.close()
        else:raise CalibrationError('未知操作')
        return dict(**self.session.status(),mode='demo',backup=str(self.journal.path))
    def close(self):
        if not self.closed and self.session:self.session.relax_all()
        self.closed=True
        return dict(closed=True,all_torque_off=True,services_restored=True,backup=str(self.journal.path),mode='demo')

class Remote:
    def __init__(self,host,journal,serial_port='auto',eeprom_only=False,auto_install=True):
        self.host=host;self.journal=journal;self.process=None;self.lines=queue.Queue()
        self.serial_port=port_argument(serial_port);self.eeprom_only=eeprom_only
        self.auto_install=auto_install;self.dest=None;self.setup_result=None
        self.setup_log=SetupLog()
        self.opts=['-o','BatchMode=yes','-o','ConnectTimeout=6','-o','ServerAliveInterval=3','-o','ServerAliveCountMax=3']
    def setup_command(self,args,timeout):
        # Merge SSH/package stderr for display, retaining the final JSON response.
        # A reader thread lets the deadline apply even to a silent SSH process.
        process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,text=True,bufsize=1,errors='replace')
        output=queue.Queue(maxsize=128)
        def reader():
            try:
                while line:=process.stdout.readline(65536):
                    if not deliver(line):return
            finally:deliver(None)
        stopped=threading.Event()
        def deliver(line):
            while not stopped.is_set():
                try:output.put(line,timeout=.1);return True
                except queue.Full:pass
            return False
        thread=threading.Thread(target=reader,daemon=True);thread.start()
        deadline=time.monotonic()+timeout;reply=None
        try:
            with (self.journal.path/'setup-stderr.log').open('a') as log:
                while True:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise subprocess.TimeoutExpired(args,timeout)
                    try:line=output.get(timeout=remaining)
                    except queue.Empty:raise subprocess.TimeoutExpired(args,timeout)
                    if line is None:break
                    log.write(line);log.flush()
                    try:data=json.loads(line)
                    except ValueError:data=None
                    if isinstance(data,dict) and 'ok' in data:
                        reply=data
                        if data.get('error'):self.setup_log.append(data['error'])
                    else:self.setup_log.append(line.rstrip())
            code=process.wait(timeout=max(.01,deadline-time.monotonic()))
            if code:raise CalibrationError(reply.get('error',f'SSH 命令退出码 {code}') if reply else f'SSH 命令退出码 {code}')
            return reply
        finally:
            stopped.set()
            if process.poll() is None:
                process.kill();process.wait(timeout=5)
            thread.join(timeout=1);process.stdout.close()
    def stage(self):
        if self.dest:return
        names=['core.py','storage.py','agent.py','board_setup.py']
        digest=hashlib.sha256(b''.join((HERE/n).read_bytes() for n in names)).hexdigest()[:16]
        dest='/tmp/duck-calibrator-'+digest+'-'+secrets.token_hex(4)
        self.setup_log.append('连接 SSH：'+self.host+'，创建临时工具目录…')
        self.setup_command(['ssh',*self.opts,self.host,'mkdir','-m','700',dest],12)
        self.setup_log.append('SSH 连接成功，正在上传环境检查与标定程序…')
        self.setup_command(['scp',*self.opts,*[str(HERE/n) for n in names],self.host+':'+dest+'/'],20)
        self.dest=dest
    def prepare(self):
        if self.process:raise CalibrationError('标定会话中不能重新准备环境或扫描串口')
        self.setup_result=None
        self.setup_log.begin()
        try:
            self.stage()
            self.setup_log.append('通过 SSH 检查 sudo、Python 和依赖（控制服务继续运行）…')
            command=['sudo','-n','python3','-u',self.dest+'/board_setup.py']
            if not self.auto_install:command.append('--no-install-deps')
            data=self.setup_command(['ssh','-T',*self.opts,self.host,shlex.join(command)],450)
            if not isinstance(data,dict) or not data.get('ok') or not data.get('ready'):
                if not isinstance(data,dict):raise CalibrationError('板端未返回有效的环境检查结果')
                raise CalibrationError(data.get('error','板端环境检查失败'))
            python=data.get('python')
            if not isinstance(python,str) or not re.fullmatch(r'/[A-Za-z0-9_./+-]+',python):
                raise CalibrationError('板端返回了无效的 Python 解释器路径')
            if not isinstance(data.get('ports'),list):raise CalibrationError('板端串口列表无效')
            for row in data['ports']:
                port_argument(row['device'])
                for alias in row.get('aliases',[]):port_argument(alias)
            self.setup_result=data
            self.journal.save('board-prepared',data)
            self.setup_log.append('依赖就绪：'+python)
            self.setup_log.append('发现串口：'+('、'.join(row['device'] for row in data['ports']) or '未发现可用串口'))
            self.setup_log.append('环境检查完成；未停止控制服务，未打开串口。')
            return data
        except (OSError,subprocess.SubprocessError,ValueError,TypeError,KeyError,CalibrationError) as exc:
            self.setup_log.append('环境准备失败：'+str(exc))
            raise CalibrationError(f'板端环境准备失败，尚未停止控制服务：{exc}。详情见 {self.journal.path}/setup-stderr.log') from exc
        finally:self.setup_log.finish()
    def start(self):
        # Always refresh inventory before maintenance: a USB device may have changed
        # since the user last opened the dropdown. Preparation never opens a UART.
        found=self.prepare()
        self.serial_port=select_port(self.serial_port,found)
        err=(self.journal.path/'ssh-stderr.log').open('w')
        flags=['--serial-port',self.serial_port]+(['--eeprom-only'] if self.eeprom_only else [])
        command=['sudo','-n',found['python'],'-u',self.dest+'/agent.py',*flags]
        self.process=subprocess.Popen(['ssh','-T',*self.opts,self.host,shlex.join(command)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,text=True,bufsize=1)
        err.close()
        def reader():
            for line in self.process.stdout:self.lines.put(line)
            self.lines.put(None)
        threading.Thread(target=reader,daemon=True).start()
        if not self.receive().get('ready'):raise CalibrationError('远端标定代理未就绪')
    def receive(self):
        try:line=self.lines.get(timeout=50)
        except queue.Empty:
            self.close_pipe()
            raise CalibrationError('远端超时，写入结果未知；不会自动重试。请托稳，查看备份和远端断力结果')
        if line is None:raise CalibrationError('远端连接已结束；请托稳，核验断力后再重连')
        return json.loads(line)
    def call(self,req):
        if self.process is None:
            if req.get('action')!='enter':raise CalibrationError('尚未连接小鸭')
            self.start()
        if self.process.poll() is not None:raise CalibrationError('远端会话已结束，请刷新重连')
        self.process.stdin.write(json.dumps(req)+'\n');self.process.stdin.flush()
        reply=self.receive()
        self.journal.save('remote-response',reply)
        if not reply.get('ok'):
            exc=CalibrationError(reply.get('error','远端会话已关闭'))
            exc.state=reply.get('result')
            raise exc
        return reply['result']
    def close_pipe(self):
        if self.process and not self.process.stdin.closed:
            try:self.process.stdin.close()
            except OSError:pass
    def close(self):
        try:
            if self.process and self.process.poll() is None:return self.call(dict(action='finish'))
        finally:self.close_pipe()

class App:
    def __init__(self,host,data,serial_port='auto',eeprom_only=False,auto_install=True):
        self.host=host;self.data=data;self.backend=None;self.lock=threading.RLock();self.csrf=secrets.token_hex(24)
        self.serial_port=port_argument(serial_port);self.eeprom_only=eeprom_only;self.auto_install=auto_install
        self.prepared_remote=None;self.preparation=None
        self.setup_log=SetupLog()
        self.motion_token=secrets.token_hex(24)
        self.last=0;self.expired=False;self.result=None;self.events=[]
    def request(self,req):
        action=req.get('action')
        if action=='probe':raise CalibrationError('自动找限位已移除，请刷新页面')
        if action=='zero' and ('ids' in req or 'detached_output' in req):
            raise CalibrationError('单独舵机对中已移除，请使用整体零位检查')
        # Revoke queued/delayed activation requests BEFORE waiting for the UART
        # owner. Otherwise a slow zero-start HTTP request could arrive after OFF.
        if action in ('relax','finish','enter'):self.motion_token=secrets.token_hex(24)
        with self.lock:
            if action=='prepare':
                if not self.host:raise CalibrationError('模拟模式不需要检查或安装板端依赖')
                if self.backend and not self.expired:raise CalibrationError('请先结束标定会话，再检查环境和串口')
                if not self.prepared_remote:
                    path=self.data/('setup-'+time.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8])
                    self.prepared_remote=Remote(self.host,Journal(path),self.serial_port,self.eeprom_only,self.auto_install)
                    self.prepared_remote.setup_log=self.setup_log
                self.preparation=None
                self.preparation=self.prepared_remote.prepare()
                return self.preparation
            if action in ('zero','hold') and req.get('motion_token')!=self.motion_token:
                raise CalibrationError('使能请求已被断力或会话切换取消，请重新确认后操作')
            if action=='enter':
                if self.backend and not self.expired:raise CalibrationError('会话已存在，请先结束')
                if req.get('supported') is not True:raise CalibrationError('请先确认小鸭已托稳在支架上')
                selected=port_argument(req.get('serial_port',self.serial_port))
                if self.host:self.preparation=None
                if self.host and self.prepared_remote:
                    self.backend=self.prepared_remote;self.prepared_remote=None
                    self.backend.serial_port=selected
                else:
                    path=self.data/(time.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8])
                    self.backend=Remote(self.host,Journal(path),selected,self.eeprom_only,self.auto_install) if self.host else Demo(path)
                    if self.host:self.backend.setup_log=self.setup_log
                self.expired=False
            if not self.backend:raise CalibrationError('请先开始标定')
            if self.expired:raise CalibrationError('页面心跳已中断，会话已结束；请刷新后重连并核验断力')
            try:
                self.result=self.backend.call(req)
                if action=='finish':self.expired=True
                return dict(**self.result,motion_token=self.motion_token)
            except Exception as exc:
                if isinstance(self.backend,Demo) and self.backend.session:
                    self.backend.session.relax_all()
                    exc.state=self.backend.session.status()
                if getattr(exc,'state',None):self.result=exc.state
                if action=='enter':
                    self.expired=True
                    if isinstance(self.backend,Remote):self.backend.close_pipe()
                raise
            finally:
                self.last=time.monotonic()
                if isinstance(self.backend,Remote) and self.backend.setup_result:
                    self.preparation=self.backend.setup_result
    def watchdog(self):
        while True:
            time.sleep(1)
            self.check_idle()
    def check_idle(self):
        with self.lock:
            if self.backend and not self.expired and self.last and time.monotonic()-self.last>10:
                self.expired=True
                try:self.result=self.backend.close()
                except Exception as exc:self.result=dict(error=str(exc),all_torque_off=False)
    def report(self):
        with self.lock:
            journal=self.backend.journal if self.backend else None
            return dict(mode='hardware' if self.host else 'demo',host=self.host,result=self.result,preparation=self.preparation,
                        events=[json.loads(s) for s in (journal.path/'journal.jsonl').read_text().splitlines()] if journal and (journal.path/'journal.jsonl').exists() else [])

def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def safe_host(self):return self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}'
        def send(self,code,data,kind='application/json; charset=utf-8'):
            if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if not self.safe_host():return self.send(403,dict(error='Host refused'))
            path=unquote(urlsplit(self.path).path)
            if path=='/api/info':return self.send(200,dict(csrf=app.csrf,motion_token=app.motion_token,mode='hardware' if app.host else 'demo',host=app.host,serial_port=app.serial_port,eeprom_only=app.eeprom_only,auto_install=app.auto_install,preparation=app.preparation,active=bool(app.backend and not app.expired),state=app.result))
            if path=='/api/report':return self.send(200,app.report())
            if path=='/api/setup-log':return self.send(200,app.setup_log.snapshot())
            root=(HERE/'static').resolve();p=(root/('index.html' if path=='/' else path.lstrip('/'))).resolve()
            if not p.is_relative_to(root) or not p.is_file():return self.send(404,dict(error='Not found'))
            self.send(200,p.read_bytes(),mimetypes.guess_type(p)[0] or 'application/octet-stream')
        def do_POST(self):
            if not self.safe_host() or self.headers.get('Origin')!=f'http://127.0.0.1:{self.server.server_port}' or self.headers.get('X-CSRF-Token')!=app.csrf or self.headers.get('Content-Type')!='application/json':return self.send(403,dict(error='Origin/token refused'))
            if self.path!='/api/action':return self.send(404,dict(error='Not found'))
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<4096:raise CalibrationError('请求过大或为空')
                req=json.loads(self.rfile.read(size))
                if not isinstance(req,dict):raise CalibrationError('请求格式错误')
                self.send(200,dict(ok=True,result=app.request(req)))
            except Exception as exc:self.send(400,dict(ok=False,error=str(exc),motion_token=app.motion_token,
                state=getattr(exc,'state',None),preparation=app.preparation,active=bool(app.backend and not app.expired)))
    return Handler

def main():
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group();g.add_argument('--demo',action='store_true');g.add_argument('--ssh')
    p.add_argument('--port',type=int,default=8795);p.add_argument('--data',type=Path,default=Path.home()/'.microduck-calibrator')
    p.add_argument('--serial-port',type=port_argument,default='auto',help='auto 自动发现，或指定板端 /dev 串口路径')
    p.add_argument('--no-install-deps',action='store_true',help='只检查依赖，不自动安装板端缺少的依赖')
    p.add_argument('--eeprom-only',action='store_true',help='只写舵机 EEPROM 零位，保留备份日志，不读写机器人标定配置文件')
    args=p.parse_args()
    if args.ssh and not re.fullmatch(r'[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]*',args.ssh):p.error('--ssh must be user@hostname or user@IPv4')
    app=App(args.ssh,args.data,args.serial_port,args.eeprom_only,not args.no_install_deps);server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(app))
    threading.Thread(target=app.watchdog,daemon=True).start()
    print(f'http://127.0.0.1:{server.server_port}/ — '+('HARDWARE: '+args.ssh if args.ssh else 'DEMO, no robot connected'),flush=True)
    def interrupted(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        with app.lock:
            if app.backend and not app.expired:
                try:print(app.backend.close())
                except Exception as exc:print('断力未核验：',exc)
        server.server_close()

if __name__=='__main__':main()
