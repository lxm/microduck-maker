import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import agent
from core import CalibrationError, FakeBus, IDS, Session, snapshot, word
from server import App, Remote
from storage import Configs, Journal


def status(cycle=1):
    return dict(mode='motion',port='/dev/ttyS2',policy_enabled=False,
        torque_off_pending=False,fresh_sample=True,all_joints_fresh=True,cycle=cycle,
        native=dict(torque_enabled=False,torque_state_confirmed=False,
            hd1910=dict(restore_pending=False,temporary_gains_applied=False),
            sample_age_ms=5,all_joints_fresh=True,held_joint_ids=[],stale_joint_ids=[],
            telemetry=dict(session='test-session'),
            joints=[dict(id=s,feedback_fresh=True) for s in IDS]))


class NativeGateTests(unittest.TestCase):
    def gate(self,a,b=None):
        with patch('agent.native_status',side_effect=[a,b or status(2)]),patch('agent.time.sleep'):
            return agent.native_off_gate()

    def test_new_samples_confirm_off(self):
        self.assertEqual(self.gate(status())['port'],'/dev/ttyS2')

    def test_frozen_or_restarted_session_rejected(self):
        for b in [status(),status(2)]:
            if b['cycle']==2:b['native']['telemetry']['session']='replacement'
            with self.subTest(b=b),self.assertRaises(CalibrationError):self.gate(status(),b)

    def test_unsafe_or_incomplete_native_feedback_refused(self):
        for key,value in [('torque_state_confirmed',None),('torque_state_confirmed',True),
                ('torque_enabled',True),('sample_age_ms',500),('all_joints_fresh',False),
                ('joints',status()['native']['joints'][:-1]),('held_joint_ids',[13]),
                ('hd1910',dict(restore_pending=True,temporary_gains_applied=False))]:
            b=status();b['native'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(CalibrationError):self.gate(b)
        b=status();b['policy_enabled']=True
        with self.assertRaises(CalibrationError):self.gate(b)

    def test_native_gate_used_when_legacy_file_absent(self):
        with patch.object(agent.HARDWARE.__class__,'exists',return_value=False),patch('agent.native_off_gate',return_value={'native':True}) as n:
            self.assertEqual(agent.off_gate(),{'native':True});n.assert_called_once()

    def test_selected_serial_port_reaches_pyserial(self):
        serial=Mock()
        with patch.dict('sys.modules',{'serial':serial}),patch('agent.fcntl.ioctl'):
            bus=agent.SerialBus('/dev/ttyS2')
        self.assertEqual(serial.Serial.call_args.args[0],'/dev/ttyS2')
        self.assertTrue(serial.Serial.call_args.kwargs['exclusive'])
        self.assertIs(bus.ser,serial.Serial.return_value)


class EepromOnlyTests(unittest.TestCase):
    def test_body_save_and_undo_never_access_config_files(self):
        with tempfile.TemporaryDirectory() as d,patch('core.time.sleep'):
            root=Path(d);config=root/'config';config.mkdir()
            sentinel=config/'hd1910-calibration.toml';sentinel.write_text('do not touch')
            journal=Journal(root/'backup');bus=FakeBus();before=copy.deepcopy(bus.rows)
            with patch('pathlib.Path.read_bytes',side_effect=AssertionError('read configuration')):
                c=Configs(config,journal,eeprom_only=True);c.validate(snapshot(bus))
                s=Session(bus,journal.save,c.commit,c.mouth)
                p=s.sample(13);s.apply(p['token'])
                self.assertEqual(word(bus.rows[13],56),2048)
                self.assertEqual([x for x in IDS if bus.rows[x][:40]!=before[x][:40]],[13])
                self.assertFalse(any(addr in (40,42,48,50,51) for _,addr,_ in bus.writes))
                s.undo(13);self.assertEqual(word(bus.rows[13],31),word(before[13],31))
            self.assertEqual(sentinel.read_text(),'do not touch')
            self.assertEqual(list(config.iterdir()),[sentinel])
            self.assertTrue((journal.path/'registers-before.json').exists())

    def test_missing_mouth_does_not_block_body_or_assume_endpoints(self):
        with tempfile.TemporaryDirectory() as d,patch('core.time.sleep'):
            journal=Journal(Path(d)/'backup');c=Configs('/does-not-exist',journal,eeprom_only=True)
            s=Session(FakeBus(),journal.save,c.commit,c.mouth)
            self.assertIsNone(s.mouth)
            p=s.sample(13);s.apply(p['token'])
            with self.assertRaisesRegex(CalibrationError,'嘴部'):s.start_zero(True)

    def test_mouth_endpoints_stay_in_session_and_journal(self):
        with tempfile.TemporaryDirectory() as d,patch('core.time.sleep'):
            root=Path(d);journal=Journal(root/'backup');c=Configs(root/'config',journal,eeprom_only=True)
            bus=FakeBus();s=Session(bus,journal.save,c.commit,c.mouth)
            bus.rows[34][56:58]=list((2050).to_bytes(2,'little'));s.sample(34,endpoint='closed')
            bus.rows[34][56:58]=list((1750).to_bytes(2,'little'));p=s.sample(34,endpoint='open');s.apply(p['token'])
            self.assertEqual(s.mouth['closed_raw'],2048);self.assertEqual(s.mouth['open_raw'],1748)
            self.assertFalse((root/'config').exists());self.assertEqual(c.mouth,s.mouth)

    def test_failed_entry_can_be_retried_with_same_server(self):
        with tempfile.TemporaryDirectory() as d:
            a=App('radxa@robot',Path(d),'/dev/ttyS2',True)
            with patch.object(Remote,'call',side_effect=CalibrationError('not off')),patch.object(Remote,'close_pipe'):
                for _ in range(2):
                    with self.assertRaisesRegex(CalibrationError,'not off'):a.request(dict(action='enter',supported=True))
                    self.assertTrue(a.expired)


if __name__=='__main__':unittest.main()
