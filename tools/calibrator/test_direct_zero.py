"""Direct web zero: final targets, original gains, bounded force and feedback."""
import copy
import unittest
from unittest.mock import Mock, patch
from core import BODY_IDS, IDS, CalibrationError, FakeBus, Session, word


class DirectZeroTests(unittest.TestCase):
    def setUp(self):
        self.clock=100.0
        timer=patch('core.time.monotonic',side_effect=lambda:self.clock);timer.start();self.addCleanup(timer.stop)
        sleeper=patch('core.time.sleep');sleeper.start();self.addCleanup(sleeper.stop)
        self.bus=FakeBus(follow_goals=True)
        for sid in BODY_IDS:
            self.bus.rows[sid][56:58]=list((2200).to_bytes(2,'little'))
            self.bus.rows[sid][50:52]=[32+sid%3,sid%2]
        self.bus.rows[31][50:52]=[32,0]
        self.bus.rows[34][56:58]=list((50).to_bytes(2,'little'))
        self.original=copy.deepcopy(self.bus.rows)
        self.commit=Mock();self.session=Session(self.bus,lambda *_:None,self.commit,None)

    def tick(self,seconds=.25):
        self.clock+=seconds
        return self.session.advance_zero()

    def arm(self):
        self.session.start_zero(True,torque_limit=600,direct=True)
        for _ in BODY_IDS:self.tick()
        self.assertEqual(self.session.zero_check['phase'],'moving')

    def complete(self):
        self.arm()
        for _ in range(50):
            self.tick()
            if self.session.zero_check['phase']=='holding':return
        self.fail('direct zero did not finish')

    def test_only_preload_then_2048_original_gains_and_600_cap(self):
        self.complete()
        for sid in BODY_IDS:
            goals=[word(data,0) for s,a,data in self.bus.writes if s==sid and a==42]
            self.assertEqual(goals,[2200,2048])
            self.assertEqual(self.bus.rows[sid][50:52],self.original[sid][50:52])
            self.assertEqual(word(self.bus.rows[sid],48),600)
            self.assertEqual(self.bus.rows[sid][:40],self.original[sid][:40])
        self.assertEqual(self.session.held,set(BODY_IDS))
        self.assertEqual(self.session.zero_check['commanded'],14)
        self.assertEqual(self.bus.rows[34],self.original[34])
        self.assertFalse(any(s==34 or a in (31,55) for s,a,_ in self.bus.writes))
        self.commit.assert_not_called()
        self.session.relax_all()
        self.assertTrue(all(self.bus.rows[s][48:52]==self.original[s][48:52] for s in IDS))

    def test_direct_profile_refuses_above_600_before_writes(self):
        with self.assertRaisesRegex(CalibrationError,'不得超过600'):
            self.session.start_zero(True,torque_limit=681,direct=True)
        self.assertFalse(self.bus.writes)

    def test_lower_original_cap_is_not_raised(self):
        for rows in (self.bus.rows,self.session.current,self.session.initial):
            rows[31 if rows is self.bus.rows else '31'][48:50]=list((250).to_bytes(2,'little'))
        self.complete();self.assertEqual(word(self.bus.rows[31],48),250)

    def test_large_transient_is_allowed_then_stall_turns_everything_off(self):
        self.bus.follow_goals=False;self.arm();self.tick()
        self.assertGreater(abs(word(self.bus.rows[10],56)-2048)*360/4096,4)
        self.assertEqual(self.session.zero_check['phase'],'moving')
        self.clock+=5.1
        with self.assertRaisesRegex(CalibrationError,'连续5秒未接近2048'):self.tick()
        self.assertEqual(self.session.zero_check['phase'],'failed')
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))
        self.assertTrue(all(self.bus.rows[s][48:52]==self.original[s][48:52] for s in IDS))

    def test_slow_progress_still_has_15_second_arrival_deadline(self):
        self.bus.follow_goals=False;self.arm()
        for _ in BODY_IDS:self.tick(.05)
        with self.assertRaisesRegex(CalibrationError,'15秒仍未到位'):
            for _ in range(4):
                for sid in BODY_IDS:
                    self.bus.rows[sid][56:58]=list((word(self.bus.rows[sid],56)-2).to_bytes(2,'little'))
                self.tick(4)
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))

    def test_error_trip_after_arrival_still_applies_to_holding(self):
        self.complete();self.bus.rows[31][56:58]=list((2100).to_bytes(2,'little'))
        with self.assertRaisesRegex(CalibrationError,'ID31 保持偏差超过4'):self.session.refresh()

    def test_stop_between_direct_targets_never_resumes(self):
        self.arm();self.tick();self.session.relax_all();self.bus.writes=[]
        for _ in range(3):self.tick()
        self.assertFalse(self.bus.writes);self.assertFalse(self.session.held)

    def test_unknown_write_outcome_is_not_retried(self):
        self.arm();original=self.bus.write
        def lost(sid,addr,data):
            result=original(sid,addr,data)
            if addr==42 and int.from_bytes(data,'little')==2048:raise OSError('lost ACK')
            return result
        with patch.object(self.bus,'write',side_effect=lost):
            with self.assertRaisesRegex(OSError,'lost ACK'):self.tick()
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))
        for _ in range(3):self.tick()
        self.assertEqual(sum(a==42 and word(d,0)==2048 for _,a,d in self.bus.writes),1)

    def test_range_health_and_configuration_checks_are_not_bypassed(self):
        self.arm();self.tick();self.bus.rows[31][63]=50
        with self.assertRaisesRegex(CalibrationError,'温度'):self.tick()
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))


if __name__=='__main__':unittest.main()
