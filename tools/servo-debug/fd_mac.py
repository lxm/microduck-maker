#!/usr/bin/env python3
"""Local FD-style HD-1910 workbench. Only pyserial is required."""
import argparse
import json
import secrets
import struct
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import serial.tools.list_ports

HERE = Path(__file__).resolve().parent
import feetech

HD_REGISTERS = dict(feetech.REGISTERS)
HD_REGISTERS.update({
    44: ('目标电流（HD mode 4 不作为可靠限流）', 2, 'SRAM'),
    50: ('位置环 P（运行值）', 1, 'SRAM'),
    51: ('位置环 D（运行值）', 1, 'SRAM'),
    52: ('位置环 I（运行值）', 1, 'SRAM'),
})
SPECIAL_REGISTERS = {5, 6, 7, 8, 31, 40, 42, 44, 55}


class DemoBus(feetech.FeetechBus):
    """Register-level simulator; uses the real calibration workflow."""
    def __init__(self):
        self._io_lock = threading.RLock()
        self.regs = {}
        self.stats = dict(tx=0, rx_ok=0, timeout=0, bad_checksum=0, bad_id=0)
        for sid in (1, 10, 20, 30):
            r = bytearray(91)
            r[0], r[1], r[5], r[8], r[33], r[55] = 3, 46, sid, 1, 4, 1
            struct.pack_into('<H', r, 3, 7946)
            struct.pack_into('<H', r, 11, 4095)
            struct.pack_into('<H', r, 42, 2300)
            struct.pack_into('<H', r, 56, 2300)
            r[62], r[63] = 51, 29
            self.regs[sid] = r

    def close(self):
        pass

    def ping(self, sid):
        return 0 if sid in self.regs else None

    def read(self, sid, addr, n):
        if sid not in self.regs:
            raise feetech.BusError('timeout: 舵机离线')
        r = self.regs[sid]
        if r[40] == 1:
            pos, goal = struct.unpack_from('<H', r, 56)[0], struct.unpack_from('<H', r, 42)[0]
            step = max(-12, min(12, goal - pos))
            struct.pack_into('<H', r, 56, pos + step)
            r[66] = int(pos != goal)
        r[67:69] = r[42:44]
        self.stats['tx'] += 1
        self.stats['rx_ok'] += 1
        return 0, bytes(r[addr:addr+n])

    def write(self, sid, addr, data, wait=True):
        r = self.regs[sid]
        r[addr:addr+len(data)] = bytes(data)
        if addr == 5:
            self.regs[data[0]] = self.regs.pop(sid)
        return 0

    def _send(self, sid, instr, params=b''):
        if instr == feetech.CALIBRATE:
            r = self.regs[sid]
            old = struct.unpack_from('<H', r, 56)[0]
            offset = struct.unpack_from('<H', r, 31)[0]
            struct.pack_into('<H', r, 31, (offset + old - 2048) & 0xFFFF)
            struct.pack_into('<H', r, 56, 2048)

    def _read_status(self, sid):
        return sid, 0, b''

    def reboot(self, sid):
        self.regs[sid][40] = 0


class Workbench:
    def __init__(self, data_dir=None):
        self.lock = threading.RLock()
        self.bus = None
        self.port = ''
        self.ids = []
        self.data_dir = Path(data_dir or Path.home() / 'Library/Application Support/MicroDuck FD')

    @staticmethod
    def number(value, low, high):
        if isinstance(value, bool) or str(value).strip() != str(int(value)):
            raise ValueError('请输入整数')
        value = int(value)
        if not low <= value <= high:
            raise ValueError(f'数值须在 {low}–{high} 之间')
        return value

    def read(self, sid, addr, size):
        err, data = self.bus.read(sid, addr, size)
        if err:
            raise ValueError(f'舵机状态异常：{err}')
        return int.from_bytes(data, 'little')

    def put(self, sid, addr, size, value):
        result = self.bus.write(sid, addr, value.to_bytes(size, 'little'))
        if result:
            raise ValueError(f'写入返回异常：{result}')
        actual = self.read(sid, addr, size)
        if actual != value:
            raise ValueError(f'写入未通过回读：期望 {value}，实际 {actual}')

    def snapshot(self, sid):
        # Check all read responses, including error status.
        raw = []
        for addr in range(0, 87, 10):
            n = min(10, 87 - addr)
            raw.extend(self.read(sid, addr, n).to_bytes(n, 'little'))
        rows = []
        for addr, (name, size, area) in sorted(HD_REGISTERS.items()):
            rows.append(dict(addr=addr, name=name, size=size, area=area,
                             value=int.from_bytes(bytes(raw[addr:addr+size]), 'little')))
        return dict(format='microduck-fd-v1', id=sid, time=time.time(),
                    demo=isinstance(self.bus, DemoBus), rows=rows, raw=raw)

    def backup(self, sid):
        data = self.snapshot(sid)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        name = self.data_dir / f"{'demo-' if data['demo'] else ''}id{sid}-{time.time_ns()}.json"
        name.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return str(name)

    def require_off(self, sid):
        if self.read(sid, 40, 1) != 0:
            raise ValueError('请托住关节并关闭扭矩，再进行此操作')

    def call(self, op, args):
        with self.lock:
            return self._call(op, args)

    def _call(self, op, a):
        if op == 'ports':
            return dict(ports=[dict(device=p.device, description=p.description)
                               for p in serial.tools.list_ports.comports()], connected=bool(self.bus),
                        port=self.port, ids=self.ids, demo=isinstance(self.bus, DemoBus))
        if op == 'connect':
            if self.bus:
                raise ValueError('请先关闭当前连接')
            baud = self.number(a.get('baud', 1000000), 38400, 1000000)
            port = a.get('port', '')
            if port == 'DEMO':
                self.bus = DemoBus()
            else:
                ports = {p.device for p in serial.tools.list_ports.comports()}
                if port not in ports:
                    raise ValueError('串口不存在，请刷新串口列表')
                self.bus = feetech.FeetechBus(port, baud)
            self.port, self.ids = port, []
            return dict(port=port, demo=port == 'DEMO')
        if op == 'disconnect':
            if self.bus:
                self.bus.close()
            self.bus, self.ids, self.port = None, [], ''
            return dict(closed=True)
        if not self.bus:
            raise ValueError('请先打开串口或演示设备')
        if op == 'scan':
            start = self.number(a.get('start', 0), 0, 253)
            end = self.number(a.get('end', 253), start, 253)
            self.ids = [i for i in range(start, end + 1) if self.bus.ping(i) is not None]
            return dict(ids=self.ids)
        if op == 'unload':
            failures = []
            for sid in self.ids:
                try:
                    self.put(sid, 40, 1, 0)
                except Exception as exc:
                    failures.append(f'{sid}: {exc}')
            return dict(unloaded=[i for i in self.ids if not any(s.startswith(f'{i}:') for s in failures)], failures=failures)
        sid = self.number(a.get('id', -1), 0, 253)
        if sid not in self.ids:
            raise ValueError('请先搜索并选择在线舵机')
        if op in ('move', 'calibrate', 'write', 'set_id', 'reboot') or (op == 'torque' and a.get('on') != 0):
            if self.read(sid, 3, 2) != 7946:
                raise ValueError('型号不是已验证布局的 HD-1910（7946）；仅开放读取与卸力')
        if op == 'state':
            s = self.bus.state(sid)
            s['torque'] = self.read(sid, 40, 1)
            s['mode'] = self.read(sid, 33, 1)
            return dict(state=s, stats=self.bus.stats)
        if op == 'torque':
            on = self.number(a['on'], 0, 1)
            if on:
                if self.read(sid, 33, 1) not in (0, 4):
                    raise ValueError('当前不是位置模式（0/4），请先核对运行模式')
                pos = self.read(sid, 56, 2)
                if not 0 <= pos <= 4095:
                    raise ValueError('当前位置超出单圈范围，拒绝使能')
                self.put(sid, 42, 2, pos)
            self.put(sid, 40, 1, on)
            return dict(torque=on)
        if op == 'move':
            if self.read(sid, 33, 1) not in (0, 4):
                raise ValueError('位置控制仅支持模式 0/4')
            if self.read(sid, 40, 1) != 1:
                raise ValueError('扭矩未开启；请先在当前位置使能')
            goal = self.number(a['position'], 0, 4095)
            speed = self.number(a.get('speed', 100), 1, 3000)
            acc = self.number(a.get('acc', 10), 1, 254)
            pos = self.read(sid, 56, 2)
            if abs(goal-pos) > 114 and a.get('confirm_large') is not True:
                raise ValueError('目标距当前位置超过 10°，请明确确认大幅运动')
            self.put(sid, 41, 1, acc)
            self.put(sid, 46, 2, speed)
            self.put(sid, 42, 2, goal)
            return dict(goal=goal)
        if op == 'stop':
            pos = self.read(sid, 56, 2)
            if not 0 <= pos <= 4095:
                raise ValueError('位置异常，请使用卸力')
            self.put(sid, 42, 2, pos)
            return dict(goal=pos)
        if op == 'dump':
            return self.snapshot(sid)
        if op == 'calibrate':
            self.require_off(sid)
            backup = self.backup(sid)
            result = self.bus.calibrate_zero(sid)
            return dict(**result, backup=backup)
        if op == 'write':
            addr = self.number(a['addr'], 0, 90)
            if addr not in HD_REGISTERS:
                raise ValueError('未知寄存器')
            name, size, area = HD_REGISTERS[addr]
            # Identity, communication and motion require dedicated workflows.
            if area not in ('EEPROM', 'SRAM') or addr in SPECIAL_REGISTERS:
                raise ValueError('此寄存器只读或须使用专用操作；串口配置请用官方 FD')
            value = self.number(a['value'], 0, (1 << (8*size))-1)
            if addr in (9, 11) and value > 4095:
                raise ValueError('单圈角度限制须在 0–4095 之间')
            if addr == 33 and value not in (0, 4):
                raise ValueError('此工具只支持位置模式 0/4')
            self.require_off(sid)
            if area == 'EEPROM':
                if a.get('confirm') is not True:
                    raise ValueError('写入 EEPROM 需要确认')
                backup = self.backup(sid)
                try:
                    self.put(sid, 55, 1, 0)
                    self.put(sid, addr, size, value)
                finally:
                    self.put(sid, 55, 1, 1)
            else:
                backup = None
                self.put(sid, addr, size, value)
            return dict(addr=addr, value=value, backup=backup)
        if op == 'set_id':
            new = self.number(a['new'], 0, 253)
            self.require_off(sid)
            if new == sid:
                raise ValueError('新旧 ID 相同')
            # The UI instructs physical isolation; do not trust a previous scan.
            found = [i for i in range(254) if self.bus.ping(i) is not None]
            if found != [sid]:
                raise ValueError('改 ID 时总线必须只连接目标这一颗舵机')
            backup = self.backup(sid)
            self.put(sid, 55, 1, 0)
            try:
                self.bus.write_u8(sid, 5, new)
                if self.bus.ping(new) is None or self.read(new, 5, 1) != new:
                    raise ValueError('新 ID 未验证，请重新搜索')
            finally:
                for candidate in (sid, new):
                    if self.bus.ping(candidate) is not None:
                        self.put(candidate, 55, 1, 1)
            self.ids = [new]
            return dict(id=new, backup=backup)
        if op == 'reboot':
            self.require_off(sid)
            self.bus.reboot(sid)
            return dict(rebooted=True)
        raise ValueError('不支持的操作')


def make_handler(workbench, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data, mime='application/json; charset=utf-8'):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def local(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.local():
                return self.reply(403, dict(error='仅允许本机访问'))
            if self.path == '/':
                html = (HERE / 'index.html').read_text().replace('__TOKEN__', token)
                return self.reply(200, html.encode(), 'text/html; charset=utf-8')
            if self.path in ('/app.css', '/app.js'):
                mime = 'text/css' if self.path.endswith('.css') else 'text/javascript'
                return self.reply(200, (HERE / self.path[1:]).read_bytes(), mime + '; charset=utf-8')
            self.reply(404, dict(error='not found'))

        def do_POST(self):
            if not self.local() or self.headers.get('X-FD-Token') != token:
                return self.reply(403, dict(error='无效本地会话，请刷新页面'))
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= 65536:
                    raise ValueError('请求大小异常')
                args = json.loads(self.rfile.read(n))
                if self.path != '/api' or not isinstance(args, dict):
                    raise ValueError('请求格式异常')
                result = workbench.call(args.pop('op'), args)
                self.reply(200, result)
            except Exception as exc:
                self.reply(400, dict(error=str(exc)))
    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--http-port', type=int, default=0)
    ap.add_argument('--no-browser', action='store_true')
    a = ap.parse_args()
    workbench = Workbench()
    http = ThreadingHTTPServer(('127.0.0.1', a.http_port), make_handler(workbench, secrets.token_urlsafe(32)))
    url = f'http://127.0.0.1:{http.server_port}'
    print(f'MicroDuck FD · {url}\n关闭此终端会结束服务；关闭串口不会自动卸力。', flush=True)
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        http.server_close()
        workbench.call('disconnect', {})


if __name__ == '__main__':
    main()
