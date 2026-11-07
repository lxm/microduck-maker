"""Calibration remains reachable without a running daemon or an already valid zero."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import agent
from core import CalibrationError, FakeBus, Session, encode_offset, position_count, snapshot, word


class PositionRecoveryTests(unittest.TestCase):
    def make_session(self,position,offset=-1910):
        bus=FakeBus()
        raw=(0x8000|abs(position)) if position<0 else position
        bus.rows[20][56:58]=list(raw.to_bytes(2,'little'))
        bus.rows[20][31:33]=list(encode_offset(offset).to_bytes(2,'little'))
        return bus,Session(bus,Mock(),Mock())

    @patch('core.time.sleep')
    def test_extended_and_negative_feedback_can_sample_save_and_undo(self,_):
        for position in (4148,-52,8190):
            with self.subTest(position=position):
                bus,s=self.make_session(position);original=word(bus.rows[20],31)
                self.assertEqual(position_count(s.initial['20']),position)
                self.assertFalse(bus.writes)
                p=s.sample(20);self.assertEqual(p['position'],position%4096)
                s.apply(p['token']);self.assertEqual(position_count(bus.rows[20]),2048)
                self.assertFalse(any(a in (40,42,48,50,51) for _,a,_ in bus.writes))
                s.undo(20);self.assertEqual(word(bus.rows[20],31),original)
                self.assertEqual(position_count(bus.rows[20])%4096,position%4096)

    @patch('core.time.sleep')
    def test_stable_sampling_across_single_turn_boundary(self,_):
        bus,s=self.make_session(4095,offset=100)
        original=bus.read;values=iter([4095,0]*10)
        def read(sid,addr,n):
            if sid==20 and addr==56:return 0,next(values).to_bytes(2,'little')
            return original(sid,addr,n)
        bus.read=read
        self.assertEqual(s.sample(20)['position'],0)

    def test_recovery_feedback_does_not_enable_motion_or_mask_health(self):
        bus,s=self.make_session(4148)
        for action in (s.start_home,lambda confirmed:s.start_zero(confirmed,direct=True,torque_limit=600)):
            with self.assertRaises(CalibrationError):action(True)
        s.completed['20']={'known_angle':0}
        with self.assertRaises(CalibrationError):s.hold(20)
        self.assertFalse(bus.writes)
        bus.rows[20][40]=1
        with self.assertRaises(CalibrationError):snapshot(bus)


class StandaloneEntryTests(unittest.TestCase):
    def run_entry(self,active,occupied=False,torque=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'opt/robot').mkdir(parents=True)
            (root/'dev').mkdir();(root/'dev/ttyS2').touch()
            running=set(active);events=[];bus=FakeBus();bus.close=Mock()
            if torque:bus.rows[20][40]=1
            def command(*args,**kw):
                events.append(args)
                if args[0]=='fuser':return Mock(returncode=0 if occupied else 1,stdout='123' if occupied else '')
                if args[1]=='is-active':return Mock(returncode=0 if args[-1] in running else 3)
                if args[1]=='stop':running.discard(args[-1])
                if args[1]=='start':running.add(args[-1])
                return Mock(returncode=0)
            with patch.dict('sys.modules',{'serial':Mock()}),patch('agent.Path',side_effect=lambda p:root/str(p).lstrip('/')),\
                    patch('agent.os.geteuid',return_value=0),patch('agent.shutil.which',return_value='/usr/bin/fuser'),\
                    patch('agent.command',side_effect=command),patch('agent.entry_gate',return_value={'direct_snapshot_required':True}),\
                    patch('agent.SerialBus',return_value=bus) as serial,patch('agent.off_gate'),patch('core.time.sleep'):
                m=agent.Maintenance('/dev/ttyS2',eeprom_only=True)
                try:
                    if occupied or torque:
                        with self.assertRaises(CalibrationError):m.enter()
                        if occupied:serial.assert_not_called()
                        self.assertFalse(bus.writes)
                    else:
                        m.enter();self.assertFalse(bus.writes)
                        self.assertEqual(set(m.stopped),set(active))
                finally:result=m.close()
                if not occupied and not torque:
                    self.assertTrue(result['all_torque_off']);self.assertTrue(result['services_restored'])
                    self.assertEqual(running,set(active))
                    self.assertEqual({a[-1] for a in events if a[:2]==('systemctl','start')},set(active))
                else:self.assertFalse(result['services_restored'])

    def test_no_daemon_running_can_enter_and_does_not_start_one_on_exit(self):self.run_entry([])
    def test_alternate_daemon_is_paused_and_restored(self):self.run_entry(['padd','robotd-kissqy-test'])
    def test_both_known_daemons_are_stopped_before_serial_access(self):self.run_entry(['padd','robotd','robotd-kissqy-test'])
    def test_unknown_uart_owner_is_not_killed_or_bypassed(self):self.run_entry(['robotd'],occupied=True)
    def test_torque_on_direct_read_still_rejects_entry(self):self.run_entry(['robotd'],torque=True)


if __name__=='__main__':unittest.main()
