import copy
import io
import unittest
from unittest.mock import Mock, patch

from core import CalibrationError, FakeBus, IDS, BODY_IDS, Session, word
import zero


class ZeroCliTests(unittest.TestCase):
    def setUp(self):
        self.clock=100.0
        self.timer=patch('core.time.monotonic',side_effect=lambda:self.clock)
        self.sleep=patch('core.time.sleep',side_effect=lambda n:setattr(self,'clock',self.clock+n))
        self.timer.start();self.sleep.start()
        self.addCleanup(self.timer.stop);self.addCleanup(self.sleep.stop)
        self.bus=FakeBus(follow_goals=True)
        for sid in IDS:self.bus.rows[sid][56:58]=list((2000 if sid==34 else 2200).to_bytes(2,'little'))
        self.events=[]
        self.s=Session(self.bus,lambda *a:self.events.append(a),Mock(),dict(schema=1,id=34,mode=4,eeprom_offset_raw=0,closed_raw=2048,open_raw=1700))

    def tick(self):
        self.clock+=.1
        return self.s.advance_zero()

    def arm(self):
        self.s.start_zero(True,torque_limit=681,speed_deg_s=3,wait_for_feedback=True)
        for _ in BODY_IDS:self.tick()

    def test_cli_profile_preserves_eeprom_caps_mouth_and_restores_ram(self):
        original=copy.deepcopy(self.bus.rows);self.arm()
        for _ in range(300):
            self.tick()
            if not self.s.zero_running():break
        self.assertEqual(self.s.zero_check['phase'],'holding')
        for sid in BODY_IDS:
            self.assertEqual(word(self.bus.rows[sid],42),2048)
            self.assertEqual(word(self.bus.rows[sid],48),681)
            self.assertEqual(self.bus.rows[sid][50:52],[5,20])
            self.assertEqual(self.bus.rows[sid][:40],original[sid][:40])
        self.assertFalse(any(a in (31,55) for _,a,_ in self.bus.writes))
        self.s.config_commit.assert_not_called()
        self.assertEqual(self.bus.rows[34],original[34])
        self.assertFalse(any(sid==34 for sid,_,_ in self.bus.writes))
        self.s.relax_all()
        self.assertTrue(all(r[40]==0 and r[48:52]==original[sid][48:52] for sid,r in self.bus.rows.items()))

    def test_removed_subset_and_detached_arguments_never_write(self):
        for options in ({'ids':[14,24]},{'detached_output':True}):
            with self.assertRaises(TypeError):self.s.start_zero(True,**options)
        self.assertFalse(self.bus.writes)

    def test_outside_range_refused_before_writes(self):
        self.bus.rows[14][56:58]=list((2897).to_bytes(2,'little'))
        with self.assertRaisesRegex(CalibrationError,'ID14 当前超出'):
            self.s.start_zero(True)
        self.assertFalse(self.bus.writes)

    def test_cancel_never_resumes(self):
        self.s.start_zero(True)
        self.tick();self.s.relax_all();self.bus.writes=[]
        for _ in range(3):self.tick()
        self.assertFalse(self.bus.writes);self.assertFalse(self.s.held)

    def test_existing_lower_limit_never_raised(self):
        self.bus.rows[30][48:50]=list((250).to_bytes(2,'little'))
        self.s.current['30'][48:50]=list((250).to_bytes(2,'little'))
        self.s.initial['30'][48:50]=list((250).to_bytes(2,'little'))
        self.arm()
        self.assertEqual(word(self.bus.rows[30],48),250)

    def test_lag_pauses_then_resumes_without_jump(self):
        self.arm()
        goal=word(self.bus.rows[30],42)
        self.bus.rows[30][56:58]=list((goal+30).to_bytes(2,'little'))
        self.bus.writes=[]
        for _ in range(10):self.tick()
        self.assertEqual(self.s.zero_check['waiting_for'],[30])
        self.assertFalse(self.bus.writes)
        self.bus.rows[30][56:58]=list(goal.to_bytes(2,'little'))
        self.tick()
        self.assertEqual(self.s.zero_check['waiting_for'],[])
        self.assertLessEqual(abs(word(self.bus.rows[30],42)-goal),4)

    def test_stuck_while_waiting_gives_readback_and_turns_all_off(self):
        self.arm();goal=word(self.bus.rows[30],42)
        self.bus.rows[30][56:58]=list((goal+30).to_bytes(2,'little'))
        self.tick();self.clock+=3.1
        with self.assertRaisesRegex(CalibrationError,'等待跟随超过3秒；目标 .*扭矩上限 681'):self.tick()
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))
        self.assertEqual(self.s.zero_check['phase'],'failed')

    def test_large_error_still_trips_immediately(self):
        self.arm();goal=word(self.bus.rows[30],42)
        self.bus.rows[30][56:58]=list((goal+50).to_bytes(2,'little'))
        with self.assertRaisesRegex(CalibrationError,'ID30 保持偏差超过4'):self.tick()
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))

    def test_cancel_after_enter_never_sends_goal_and_always_closes(self):
        m=Mock(session=self.s)
        m.journal.path='/demo';m.close.return_value=dict(all_torque_off=True,services_restored=True)
        stopped=Mock(side_effect=[False,True,True])
        self.assertEqual(zero.run_inspection(m,stopped,output=lambda _:None),130)
        self.assertFalse(self.bus.writes);m.close.assert_called_once()

    def test_motion_exception_closes_and_reports_failure(self):
        m=Mock(session=self.s);m.journal.path='/demo'
        m.close.side_effect=lambda:(self.s.relax_all() and dict(all_torque_off=True,services_restored=True))
        with patch.object(self.s,'advance_zero',side_effect=OSError('UART disconnected')):
            self.assertEqual(zero.run_inspection(m,lambda:False,output=lambda _:None),1)
        m.close.assert_called_once()

    def test_success_waits_bounded_time_and_restores(self):
        m=Mock(session=self.s);m.journal.path='/demo'
        m.close.side_effect=lambda:(self.s.relax_all() and dict(all_torque_off=True,services_restored=True))
        self.assertEqual(zero.run_inspection(m,lambda:False,hold_seconds=.2,output=lambda _:None),0)
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))

    def test_failed_entry_does_not_claim_torque_off(self):
        m=Mock();m.enter.side_effect=CalibrationError('torque on')
        m.close.return_value=dict(all_torque_off=True,services_restored=True)
        output=[]
        self.assertEqual(zero.run_inspection(m,lambda:False,output=output.append),1)
        self.assertFalse(any('已核验全部断力' in s for s in output))

    def test_check_never_constructs_maintenance_or_opens_uart(self):
        with patch('agent.off_gate',return_value=dict(servo_count=15)),patch('agent.Maintenance') as m,patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(zero.main(['--check']),0)
            m.assert_not_called()


if __name__=='__main__':unittest.main()
