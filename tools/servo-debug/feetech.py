# -*- coding: utf-8 -*-
"""飞特 STS/SMS 总线的最小实现：包格式、读写、同步读写、重启。

对照 docs/飞特资料/SCS通信协议-v1.0-2026-06-09.md 和 磁编码STS内存表手册-v1.1-2026-08-27.md 写的，
不依赖飞特 SDK。字节序按磁编码系列：小端。
"""
import struct
import threading
import time

import serial

PING, READ, WRITE, REG_WRITE, ACTION = 0x01, 0x02, 0x03, 0x04, 0x05
RECOVERY, REBOOT, BACKUP, RESET_TURNS, CALIBRATE = 0x06, 0x08, 0x09, 0x0A, 0x0B
SYNC_READ, SYNC_WRITE = 0x82, 0x83
BROADCAST = 0xFE

# 内存表：地址 -> (名字, 字节数, 区)。给寄存器导出用。
REGISTERS = {
    0: ("固件主版本", 1, "只读"), 1: ("固件次版本", 1, "只读"), 2: ("END 字节序(0=小端)", 1, "只读"),
    3: ("舵机主版本", 1, "只读"), 4: ("舵机次版本", 1, "只读"),
    5: ("ID", 1, "EEPROM"), 6: ("波特率(0=1M)", 1, "EEPROM"), 7: ("预留/应答延时", 1, "EEPROM"),
    8: ("应答状态级别", 1, "EEPROM"), 9: ("最小角度限制", 2, "EEPROM"), 11: ("最大角度限制", 2, "EEPROM"),
    13: ("最高温度上限", 1, "EEPROM"), 14: ("最高输入电压", 1, "EEPROM"), 15: ("最低输入电压", 1, "EEPROM"),
    16: ("最大扭矩", 2, "EEPROM"), 18: ("相位", 1, "EEPROM"), 19: ("卸载条件", 1, "EEPROM"),
    20: ("LED报警条件", 1, "EEPROM"), 21: ("位置环P", 1, "EEPROM"), 22: ("位置环D", 1, "EEPROM"),
    23: ("位置环I", 1, "EEPROM"), 24: ("最小启动扭矩", 1, "EEPROM"), 25: ("积分限制值", 1, "EEPROM"),
    26: ("正向不灵敏区", 1, "EEPROM"), 27: ("负向不灵敏区", 1, "EEPROM"), 28: ("保护电流", 2, "EEPROM"),
    30: ("角度分辨率", 1, "EEPROM"), 31: ("位置偏移", 2, "EEPROM"), 33: ("运行模式", 1, "EEPROM"),
    34: ("保持扭矩", 1, "EEPROM"), 35: ("保护时间", 1, "EEPROM"), 36: ("过载扭矩", 1, "EEPROM"),
    37: ("速度环P", 1, "EEPROM"), 38: ("过流保护时间", 1, "EEPROM"), 39: ("速度环I", 1, "EEPROM"),
    40: ("扭矩开关", 1, "SRAM"), 41: ("加速度", 1, "SRAM"), 42: ("目标位置", 2, "SRAM"),
    44: ("PWM开环速度", 2, "SRAM"), 46: ("运行速度", 2, "SRAM"), 48: ("转矩限制", 2, "SRAM"),
    55: ("锁标志", 1, "SRAM"),
    56: ("当前位置", 2, "反馈"), 58: ("当前速度", 2, "反馈"), 60: ("当前负载", 2, "反馈"),
    62: ("当前电压(0.1V)", 1, "反馈"), 63: ("当前温度(°C)", 1, "反馈"), 64: ("异步写标志", 1, "反馈"),
    65: ("舵机状态位", 1, "反馈"), 66: ("移动标志", 1, "反馈"), 67: ("目标位置(回读)", 2, "反馈"),
    69: ("当前电流(6.5mA)", 2, "反馈"),
    80: ("移动速度阀值", 1, "出厂"), 81: ("DTs(ms)", 1, "出厂"), 82: ("速度单位系数", 1, "出厂"),
    83: ("最小速度限制", 1, "出厂"), 84: ("最大速度限制", 1, "出厂"), 85: ("加速度限制", 1, "出厂"),
    86: ("加速度倍数", 1, "出厂"),
}
STATUS_BITS = {0: "电压", 1: "磁编码", 2: "温度", 3: "电流", 5: "负载"}


def sign15(v):
    """BIT15 是方向位的 16 位量（位置、速度、电流）。"""
    return -(v & 0x7FFF) if v & 0x8000 else v


def sign10(v):
    """BIT10 是方向位（负载）。"""
    return -(v & 0x3FF) if v & 0x400 else v


class BusError(Exception):
    pass


class FeetechBus:
    def __init__(self, port, baud=1_000_000, timeout=0.02):
        self.ser = serial.Serial(port, baud, timeout=timeout, write_timeout=0.1)
        self._io_lock = threading.RLock()
        self.stats = {"tx": 0, "rx_ok": 0, "timeout": 0, "bad_checksum": 0, "bad_id": 0}

    def close(self):
        self.ser.close()

    # ---- 包 ----
    @staticmethod
    def _packet(sid, instr, params=b""):
        length = len(params) + 2
        chk = (~(sid + length + instr + sum(params))) & 0xFF
        return bytes([0xFF, 0xFF, sid, length, instr]) + bytes(params) + bytes([chk])

    def _send(self, sid, instr, params=b""):
        self.ser.reset_input_buffer()
        self.ser.write(self._packet(sid, instr, params))
        self.stats["tx"] += 1

    def _read_status(self, expect_id=None):
        """读一个应答帧，返回 (id, error, params)。"""
        # 找 FF FF
        hdr = b""
        deadline = time.monotonic() + self.ser.timeout * 3
        while time.monotonic() < deadline:
            b = self.ser.read(1)
            if not b:
                break
            hdr = (hdr + b)[-2:]
            if hdr == b"\xff\xff":
                break
        if hdr != b"\xff\xff":
            self.stats["timeout"] += 1
            raise BusError("timeout")
        rest = self.ser.read(2)
        if len(rest) < 2:
            self.stats["timeout"] += 1
            raise BusError("timeout(id/len)")
        sid, length = rest[0], rest[1]
        body = self.ser.read(length)
        if len(body) < length:
            self.stats["timeout"] += 1
            raise BusError("timeout(body)")
        err, params, chk = body[0], body[1:-1], body[-1]
        if ((~(sid + length + err + sum(params))) & 0xFF) != chk:
            self.stats["bad_checksum"] += 1
            raise BusError("bad checksum")
        if expect_id is not None and sid != expect_id:
            self.stats["bad_id"] += 1
            raise BusError(f"expected id {expect_id}, got {sid}")
        self.stats["rx_ok"] += 1
        return sid, err, bytes(params)

    # ---- 指令 ----
    def ping(self, sid):
        with self._io_lock:
            self._send(sid, PING)
            try:
                _, err, _ = self._read_status(sid)
                return err
            except BusError:
                return None

    def read(self, sid, addr, n):
        with self._io_lock:
            self._send(sid, READ, bytes([addr, n]))
            _, err, params = self._read_status(sid)
            if len(params) != n:
                raise BusError(f"short read {len(params)}/{n}")
            return err, params

    def write(self, sid, addr, data, wait=True):
        with self._io_lock:
            self._send(sid, WRITE, bytes([addr]) + bytes(data))
            if not wait or sid == BROADCAST:
                return None
            try:
                _, err, _ = self._read_status(sid)
                return err
            except BusError as e:
                # 应答级别 0 时写指令不回包，不算错
                if "timeout" in str(e):
                    return None
                raise

    def write_u8(self, sid, addr, v):
        return self.write(sid, addr, [v & 0xFF])

    def write_u16(self, sid, addr, v):
        return self.write(sid, addr, struct.pack("<H", v & 0xFFFF))

    def read_u8(self, sid, addr):
        return self.read(sid, addr, 1)[1][0]

    def read_u16(self, sid, addr):
        return struct.unpack("<H", self.read(sid, addr, 2)[1])[0]

    def sync_write(self, addr, length, id_values):
        """id_values: [(id, bytes), ...]，每个 bytes 长度 = length。"""
        params = bytes([addr, length])
        for sid, data in id_values:
            assert len(data) == length
            params += bytes([sid]) + bytes(data)
        with self._io_lock:
            self._send(BROADCAST, SYNC_WRITE, params)

    def sync_read(self, ids, addr, n):
        """返回 {id: (err, params) 或 None}，顺序按 ids。任何一颗超时不影响后面的。"""
        out = {}
        with self._io_lock:
            self._send(BROADCAST, SYNC_READ, bytes([addr, n]) + bytes(ids))
            for sid in ids:
                try:
                    _, err, params = self._read_status(sid)
                    out[sid] = (err, params) if len(params) == n else None
                except BusError:
                    out[sid] = None
        return out

    def reboot(self, sid):
        """0x08，无应答，约 800 ms 后回来。"""
        with self._io_lock:
            self._send(sid, REBOOT)

    def unlock(self, sid):
        return self.write_u8(sid, 55, 0)

    def lock(self, sid):
        return self.write_u8(sid, 55, 1)

    def calibrate_zero(self, sid):
        """HD/HLS 0x0B: redefine the supported, torque-off pose as 2048.

        Never enable torque, including on failure. Verify actual feedback rather
        than trusting the success ACK returned by unsupported firmware commands.
        """
        if not 0 <= sid <= 253:
            raise ValueError("校准只允许单颗 ID 0–253，禁止广播")
        with self._io_lock:
            def checked_read(addr, size):
                err, data = self.read(sid, addr, size)
                if err:
                    raise BusError(f"id {sid} 状态异常 {err}")
                return int.from_bytes(data, "little")

            if checked_read(40, 1) != 0:
                raise BusError("请先托住关节、关闭该舵机扭矩，再摆到零位")
            before = {"position": checked_read(56, 2), "offset": checked_read(31, 2)}
            try:
                self.write(sid, 55, [0])
                if checked_read(55, 1) != 0:
                    raise BusError("EEPROM 解锁未生效")
                self._send(sid, CALIBRATE)
                try:
                    _, err, _ = self._read_status(sid)
                    if err:
                        raise BusError(f"校准返回状态异常 {err}")
                except BusError as exc:
                    if "timeout" not in str(exc):
                        raise
                # Firmware may ACK before the new coordinate is visible.
                # Poll readback only; never resend the EEPROM calibration command.
                deadline = time.monotonic() + 0.5
                while True:
                    after = {"position": checked_read(56, 2), "offset": checked_read(31, 2)}
                    if abs(after["position"] - 2048) <= 8:
                        break
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.02)
                if abs(after["position"] - 2048) > 8:
                    raise BusError(f"校准未通过：位置 {after['position']}，应接近 2048；请保持关节不动并核对固件")
                if abs(before["position"] - 2048) > 8 and before["offset"] == after["offset"]:
                    raise BusError("校准未通过：偏移寄存器未改变")
                self.write(sid, 40, [0])
                if checked_read(40, 1) != 0:
                    raise BusError("校准后卸力未通过回读")
                # Clear stale targets in the old coordinate system before any
                # later, explicit torque enable from the operator.
                self.write(sid, 42, struct.pack("<H", after["position"]))
                if checked_read(42, 2) != after["position"]:
                    raise BusError("新坐标目标写入未验证，保持断扭")
                return {"before": before, "after": after}
            finally:
                try:
                    self.write(sid, 40, [0])
                    if checked_read(40, 1) != 0:
                        raise BusError("校准后关闭扭矩失败，请托住机身检查连接")
                finally:
                    self.write(sid, 55, [1])
                    if checked_read(55, 1) != 1:
                        raise BusError("EEPROM 重新上锁失败，请检查连接；保持断扭")

    # ---- 组合动作 ----
    def state(self, sid):
        """56..70：位置 速度 负载 电压 温度 异步写 状态 移动 目标 电流 = 15 字节。"""
        err, p = self.read(sid, 56, 15)
        return self._decode_state(err, p)

    @staticmethod
    def _decode_state(err, p):
        pos, spd, load = struct.unpack_from("<HHH", p, 0)
        goal, cur = struct.unpack_from("<HH", p, 11)
        return {
            "err": err, "pos": sign15(pos), "speed": sign15(spd), "load": sign10(load),
            "volt": p[6] / 10.0, "temp": p[7], "status": p[9], "moving": p[10],
            "goal": sign15(goal), "current_ma": sign15(cur) * 6.5,
            "status_text": "".join(n for b, n in STATUS_BITS.items() if p[9] >> b & 1) or "",
        }

    def states(self, ids):
        """一次 sync_read 拿全部，失败的 ID 值为 None。"""
        raw = self.sync_read(ids, 56, 15)
        return {sid: (self._decode_state(*v) if v else None) for sid, v in raw.items()}

    def dump(self, sid):
        """0–90 全读，按 REGISTERS 解码。"""
        raw = b""
        for start in range(0, 91, 10):
            n = min(10, 91 - start)
            for attempt in range(4):
                try:
                    raw += self.read(sid, start, n)[1]
                    break
                except BusError as e:
                    if attempt == 3:
                        raise BusError(f"addr {start}: {e}")
                    time.sleep(0.01)
        rows = []
        for addr in sorted(REGISTERS):
            name, size, area = REGISTERS[addr]
            v = raw[addr] if size == 1 else struct.unpack_from("<H", raw, addr)[0]
            rows.append({"addr": addr, "name": name, "area": area, "size": size, "value": v})
        return {"rows": rows, "raw": list(raw)}

    def set_id(self, old, new):
        self.unlock(old)
        self.write_u8(old, 5, new)
        self.lock(new)
        return self.ping(new) is not None
