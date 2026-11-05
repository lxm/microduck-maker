"""Offline checks for HD-1910 calibration; no serial hardware is accessed."""
import threading
import unittest

import feetech


class RegisterBus(feetech.FeetechBus):
    def __init__(self, effective=True, ack=True):
        self._io_lock = threading.RLock()
        self.reg = {40: 0, 55: 1, 56: 2500, 31: 0, 42: 1800}
        self.effective, self.ack = effective, ack
        self.writes = []
        self.instructions = []
        self.pending_write = False

    def read(self, sid, addr, n):
        if self.pending_write:
            raise feetech.BusError('unconsumed write response')
        return 0, self.reg[addr].to_bytes(n, 'little')

    def write(self, sid, addr, data, wait=True):
        self.pending_write = not wait
        value = int.from_bytes(bytes(data), 'little')
        self.writes.append((addr, value))
        self.reg[addr] = value
        return 0

    def _send(self, sid, instr, params=b''):
        self.instructions.append((sid, instr, params))
        if self.effective:
            self.reg[31] = self.reg[56] - 2048
            self.reg[56] = 2048

    def _read_status(self, sid):
        if not self.ack:
            raise feetech.BusError('timeout')
        return sid, 0, b''


class CalibrationTests(unittest.TestCase):
    def test_success_and_no_write_ack(self):
        for ack in (True, False):
            with self.subTest(ack=ack):
                bus = RegisterBus(ack=ack)
                result = bus.calibrate_zero(20)
                self.assertEqual(result['before']['position'], 2500)
                self.assertEqual(result['after']['position'], 2048)
                self.assertEqual(bus.instructions, [(20, 0x0B, b'')])
                self.assertEqual(bus.reg[42], 2048)
                self.assertEqual(bus.reg[55], 1)
                self.assertFalse(any(addr == 40 and value != 0 for addr, value in bus.writes))

    def test_firmware_enabling_torque_is_explicitly_undone(self):
        bus = RegisterBus()
        send = bus._send
        def enabling(sid, instr, params=b''):
            send(sid, instr, params)
            bus.reg[40] = 1
        bus._send = enabling
        bus.calibrate_zero(20)
        self.assertEqual(bus.reg[40], 0)
        self.assertFalse(any(addr == 40 and value == 1 for addr, value in bus.writes))

    def test_delayed_feedback_does_not_repeat_calibration(self):
        bus = RegisterBus()
        read = bus.read
        pending = [0]
        def delayed(sid, addr, size):
            if bus.instructions and addr == 56 and pending[0] < 2:
                pending[0] += 1
                return 0, (2500).to_bytes(size, 'little')
            return read(sid, addr, size)
        bus.read = delayed
        result = bus.calibrate_zero(20)
        self.assertEqual(result['after']['position'], 2048)
        self.assertEqual(len(bus.instructions), 1)
        self.assertEqual(bus.reg[40], 0)

    def test_success_ack_without_effect_is_rejected(self):
        bus = RegisterBus(effective=False)
        with self.assertRaisesRegex(feetech.BusError, '校准未通过'):
            bus.calibrate_zero(20)
        self.assertEqual(bus.reg[55], 1)
        self.assertEqual(bus.reg[40], 0)
        self.assertEqual(bus.reg[42], 1800)

    def test_torque_on_refused_before_any_write(self):
        bus = RegisterBus()
        bus.reg[40] = 1
        with self.assertRaisesRegex(feetech.BusError, '关闭'):
            bus.calibrate_zero(20)
        self.assertEqual(bus.writes, [])
        self.assertEqual(bus.instructions, [])

    def test_invalid_and_broadcast_ids_refused(self):
        for sid in (-1, 254, 255):
            bus = RegisterBus()
            with self.assertRaises(ValueError):
                bus.calibrate_zero(sid)
            self.assertEqual(bus.writes, [])

    def test_feedback_failure_relocks_without_torque_enable(self):
        bus = RegisterBus()
        read = bus.read
        def fail_after_command(sid, addr, size):
            if bus.instructions and addr == 56:
                raise feetech.BusError('timeout')
            return read(sid, addr, size)
        bus.read = fail_after_command
        with self.assertRaises(feetech.BusError):
            bus.calibrate_zero(20)
        self.assertEqual(bus.reg[55], 1)
        self.assertEqual(bus.reg[40], 0)

    def test_lock_method_is_callable(self):
        # Regression: the old serial mutex shadowed the EEPROM lock() method.
        from unittest.mock import patch
        with patch('feetech.serial.Serial'):
            bus = feetech.FeetechBus('test')
        self.assertTrue(callable(bus.lock))

    def test_packet_matches_manufacturer_example(self):
        self.assertEqual(feetech.FeetechBus._packet(1, feetech.CALIBRATE), bytes.fromhex('FF FF 01 02 0B F1'))


if __name__ == '__main__':
    unittest.main()
