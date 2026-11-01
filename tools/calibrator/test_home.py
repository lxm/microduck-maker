"""Fixed HOME goals: direction, no EEPROM, stop/fault cleanup and API guards."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from core import BODY_IDS, HOME, IDS, CalibrationError, FakeBus, Session, word
from server import App, Demo
import agent


class HomeTests(unittest.TestCase):
    def setUp(self):
        self.clock=100.
        timer=patch('core.time.monotonic',side_effect=lambda:self.clock);timer.start();self.addCleanup(timer.stop)
        sleeper=patch('core.time.sleep');sleeper.start();self.addCleanup(sleeper.stop)
        self.bus=FakeBus(follow_goals=True)
        self.initial=copy.deepcopy(self.bus.rows)
        self.commit=Mock();self.events=[]
        self.s=Session(self.bus,lambda *event:self.events.append(event),self.commit,None)

    def tick(self,dt=.25):
        self.clock+=dt
        return self.s.advance_zero()

    def complete(self):
        self.s.start_home(True)
        for _ in range(50):
            self.tick()
            if self.s.zero_check['phase']=='holding':return
        self.fail('HOME did not settle')

    def test_home_uses_negative_direction_fixed_goals_original_gains_no_eeprom(self):
        self.complete()
        targets={str(s):round(2048-HOME[s]*4096/360) for s in BODY_IDS}
        self.assertEqual(self.s.zero_check['targets'],targets)
        self.assertEqual(self.s.zero_check['pose'],'home')
        self.assertEqual(self.s.zero_check['max_error_deg'],0)
        self.assertEqual(self.s.zero_check['progress'],100)
        for sid in BODY_IDS:
            self.assertEqual(word(self.bus.rows[sid],42),targets[str(sid)])
            goals=[word(d,0) for s,a,d in self.bus.writes if s==sid and a==42]
            self.assertEqual(goals,[word(self.initial[sid],56),targets[str(sid)]])
            self.assertEqual(self.bus.rows[sid][50:52],self.initial[sid][50:52])
            self.assertEqual(word(self.bus.rows[sid],48),600)
            self.assertEqual(self.bus.rows[sid][:40],self.initial[sid][:40])
        self.assertEqual(self.bus.rows[34],self.initial[34])
        self.assertFalse(any(s==34 or a in (31,55) for s,a,d in self.bus.writes))
        self.commit.assert_not_called()
        self.assertIn('home-held',[e[0] for e in self.events])
        self.s.relax_all()
        for sid in IDS:
            self.assertEqual(self.bus.rows[sid][40],0)
            self.assertEqual(self.bus.rows[sid][48:52],self.initial[sid][48:52])

    def test_home_requires_confirmation_before_any_write(self):
        for value in (False,None,1,'true'):
            with self.assertRaises(CalibrationError):self.s.start_home(value)
        self.assertFalse(self.bus.writes)

    def test_partial_home_stops_and_cannot_resume(self):
        self.s.start_home(True)
        for _ in range(16):self.tick()
        self.s.relax_all();self.bus.writes=[]
        for _ in range(3):self.tick()
        self.assertFalse(self.bus.writes);self.assertFalse(self.s.held)

    def test_home_stall_uses_home_target_then_releases_all(self):
        self.bus.follow_goals=False;self.s.start_home(True)
        for _ in range(18):self.tick(.05)
        with self.assertRaisesRegex(CalibrationError,'回HOME连续5秒未接近'):
            self.tick(5.1)
        self.assertEqual(self.s.zero_check['phase'],'failed')
        self.assertTrue(all(self.bus.rows[s][40]==0 for s in IDS))

    def test_home_holding_error(self):
        self.complete();self.bus.rows[31][56:58]=list((word(self.bus.rows[31],42)+50).to_bytes(2,'little'))
        with self.assertRaisesRegex(CalibrationError,'ID31 保持偏差'):self.s.refresh()

    def test_home_arrival_deadline_with_continuous_slow_progress(self):
        self.bus.follow_goals=False;self.s.start_home(True)
        for _ in range(28):self.tick(.05)
        with self.assertRaisesRegex(CalibrationError,'回HOME15秒仍未到位'):
            for _ in range(4):
                for sid in BODY_IDS:
                    current=word(self.bus.rows[sid],56);target=self.s.zero_check['targets'][str(sid)]
                    new=current+max(-2,min(2,target-current))
                    self.bus.rows[sid][56:58]=list(new.to_bytes(2,'little'))
                self.tick(4)
        self.assertTrue(all(self.bus.rows[s][40]==0 for s in IDS))

    def test_home_preserves_lower_cap_and_checks_health(self):
        for rows,key in ((self.bus.rows,31),(self.s.current,'31'),(self.s.initial,'31')):
            rows[key][48:50]=list((250).to_bytes(2,'little'))
        self.complete();self.assertEqual(word(self.bus.rows[31],48),250)
        self.s.start_home(True);self.bus.rows[31][63]=50
        with self.assertRaisesRegex(CalibrationError,'温度'):self.tick()
        self.assertTrue(all(self.bus.rows[s][40]==0 for s in IDS))

    def test_zero_can_follow_home_without_changing_eeprom(self):
        self.complete();self.s.start_zero(True,torque_limit=600,direct=True)
        for _ in range(50):
            self.tick()
            if self.s.zero_check['phase']=='holding':break
        self.assertEqual(self.s.zero_check['pose'],'zero')
        self.assertTrue(all(word(self.bus.rows[s],56)==2048 for s in BODY_IDS))
        self.assertFalse(any(a in (31,55) for s,a,d in self.bus.writes))

    def test_demo_and_board_routes_use_fixed_home_method(self):
        board=object.__new__(agent.Maintenance);board.session=Mock();board.status=Mock(return_value={})
        board.handle(dict(action='home',confirmed=True));board.session.start_home.assert_called_once_with(True)
        with tempfile.TemporaryDirectory() as tmp:
            demo=Demo(Path(tmp)/'demo');demo.call(dict(action='enter',supported=True))
            result=demo.call(dict(action='home',confirmed=True))
            self.assertEqual(result['zero_check']['pose'],'home');demo.close()

    def test_activation_token_and_custom_target_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=App(None,Path(tmp));app.request(dict(action='enter',supported=True))
            for req in [dict(action='home',confirmed=True),dict(action='home',confirmed=True,motion_token=app.motion_token,targets={'10':2048})]:
                with self.assertRaises(CalibrationError):app.request(req)
            self.assertFalse(app.backend.bus.writes)
            app.request(dict(action='home',confirmed=True,motion_token=app.motion_token))
            self.assertEqual(app.result['zero_check']['pose'],'home')
            token=app.motion_token;app.request(dict(action='relax'))
            with self.assertRaises(CalibrationError):app.request(dict(action='home',confirmed=True,motion_token=token))
            app.request(dict(action='finish'))

    def test_home_model_and_control_share_reference_degrees(self):
        # Guard the actual published 3D reference, including left/right signs.
        text=(Path(__file__).parent/'static/source/joint-reference.js').read_text()
        import re
        block=text.split('officialStandingDegrees=Object.freeze({',1)[1].split('});',1)[0]
        degrees={int(s):float(v) for s,v in re.findall(r'(\d+):(-?[\d.]+)',block)}
        self.assertEqual(HOME,degrees)


if __name__=='__main__':unittest.main()
