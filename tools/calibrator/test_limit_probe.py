import copy
import json
import select
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import CalibrationError, FakeBus, IDS, Session, word
from agent import Maintenance, SerialBus
from server import App


class LimitProbeTests(unittest.TestCase):
    def setUp(self):
        self.now=100.0
        p=patch('core.time.monotonic',side_effect=lambda:self.now);p.start();self.addCleanup(p.stop)
        p=patch('core.time.sleep',return_value=None);p.start();self.addCleanup(p.stop)
        self.bus=FakeBus(follow_goals=True)
        self.bus.rows[13][56:58]=list((2048).to_bytes(2,'little'))
        self.bus.stops={13:(2002,2094)}
        self.before=copy.deepcopy(self.bus.rows);self.events=[];self.persist=Mock()
        self.s=Session(self.bus,lambda *x:self.events.append(copy.deepcopy(x)),self.persist)

    def start(self,**kw):
        opts=dict(sid=13,direction=1,confirmed=True)
        opts.update(kw);return self.s.start_probe(**opts)

    def tick(self,delta=.1):
        self.now+=delta;return self.s.advance_probe()

    def finish(self):
        for _ in range(3000):
            if not self.s.probe.running():return self.s.probe.result
            self.tick()
        self.fail('probe did not terminate')

    def assert_measurement_only(self):
        self.persist.assert_not_called()
        self.assertFalse(any(a in (31,55) for _,a,_ in self.bus.writes))
        self.assertTrue(all(sid==13 for sid,a,d in self.bus.writes if a==42 or (a==40 and d==[1])))
        for sid in IDS:
            self.assertEqual(self.bus.rows[sid][:40],self.before[sid][:40])
            self.assertEqual(self.bus.rows[sid][55],self.before[sid][55])
            self.assertEqual(self.bus.rows[sid][48:52],self.before[sid][48:52])
            self.assertEqual(self.bus.rows[sid][40],0)

    def test_three_contacts_returns_origin_and_restores_without_eeprom(self):
        self.start();r=self.finish()
        self.assertEqual(r['phase'],'complete')
        self.assertEqual([x['position_raw'] for x in r['samples']],[2094]*3)
        self.assertEqual(r['statistics']['spread_counts'],0)
        self.assertEqual(word(self.bus.rows[13],56),2048)
        self.assertTrue(r['eeprom_unchanged']);self.assert_measurement_only()
        goals=[int.from_bytes(bytes(d),'little') for s,a,d in self.bus.writes if a==42]
        self.assertEqual(goals[0],2048)
        # The only non-ramp change removes a <= 12 count contact preload.
        self.assertLessEqual(max(goals),2106)

    def test_negative_direction_and_nonzero_repeatability_statistics(self):
        self.start(direction=-1)
        while self.s.probe.running():
            count=len(self.s.probe.result['samples'])
            self.bus.stops={13:(2002+count,2094)}
            self.tick()
        r=self.s.probe.result
        self.assertEqual(r['phase'],'complete')
        self.assertEqual([x['position_raw'] for x in r['samples']],[2002,2003,2004])
        self.assertEqual(r['statistics']['spread_counts'],2)
        self.assertAlmostEqual(r['statistics']['spread_deg'],2*360/4096)
        self.assertAlmostEqual(r['statistics']['std_counts'],(2/3)**.5)
        self.assert_measurement_only()

    def test_no_contact_never_extends_window(self):
        self.bus.stops={};self.start(travel_deg=3)
        r=self.finish();self.assertEqual(r['phase'],'no_contact')
        self.assertEqual(r['samples'],[])
        self.assertEqual(word(self.bus.rows[13],56),2082)
        self.assert_measurement_only()

    def test_slow_fresh_sampling_still_measures_repeated_contacts(self):
        self.start(repeats=2)
        for _ in range(300):
            if not self.s.probe.running():break
            self.tick(.4)
        self.assertEqual(self.s.probe.result['phase'],'complete')
        self.assertEqual(len(self.s.probe.result['samples']),2)
        self.assert_measurement_only()

    def test_stuck_from_start_is_not_a_successful_contact(self):
        self.bus.stops={13:(2048,2048)};self.start()
        with self.assertRaisesRegex(CalibrationError,'有效运动'):self.finish()
        self.assertEqual(self.s.probe.result['phase'],'failed')
        self.assertEqual(self.s.probe.result['samples'],[])
        self.assert_measurement_only()

    def test_invalid_inputs_make_no_writes(self):
        for kw in (dict(direction=True),dict(direction=0),dict(sid=254),dict(confirmed=False),
                   dict(travel_deg=float('nan')),dict(travel_deg=21),dict(speed_deg_s=3),
                   dict(torque_limit=201),dict(repeats=1),dict(repeats=True)):
            with self.assertRaises(CalibrationError):self.start(**kw)
        self.assertFalse(self.bus.writes)

    def test_wraparound_rejected(self):
        self.bus.rows[13][56:58]=list((4090).to_bytes(2,'little'))
        with self.assertRaisesRegex(CalibrationError,'单圈边界'):self.start()
        self.assertFalse(self.bus.writes)

    def test_lower_existing_output_cap_is_never_raised(self):
        for row in (self.bus.rows[13],self.s.current['13'],self.s.initial['13'],self.before[13]):
            row[48:50]=list((70).to_bytes(2,'little'))
        self.start(torque_limit=150);self.tick()
        self.assertEqual(word(self.bus.rows[13],48),70)
        self.s.relax_all();self.assert_measurement_only()

    def test_initial_preload_uses_current_position_at_low_output(self):
        self.start();self.assertFalse(self.bus.writes);self.tick()
        first=next(i for i,w in enumerate(self.bus.writes) if w[1]==42)
        self.assertTrue(all(any(w[1]==a for w in self.bus.writes[:first]) for a in (48,50,51)))
        self.assertEqual(self.bus.writes[first],(13,42,[0,8]))

    def test_stop_before_arming_never_sends_a_goal_and_cannot_resume(self):
        self.start();self.s.relax_all();self.tick()
        self.assertEqual(self.s.probe.result['phase'],'cancelled')
        self.assertFalse(any(a==42 for _,a,_ in self.bus.writes));self.assert_measurement_only()

    def test_active_stop_disables_selected_joint_first(self):
        self.start();self.tick();self.bus.writes=[];self.s.relax_all()
        self.assertEqual(self.bus.writes[0],(13,40,[0]))
        self.assert_measurement_only()

    def test_stale_control_loop_aborts_without_catchup_motion(self):
        self.start();self.tick();self.bus.writes=[]
        with self.assertRaisesRegex(CalibrationError,'超过2秒'):self.tick(2.1)
        self.assertFalse(any(a==42 for _,a,_ in self.bus.writes));self.assert_measurement_only()

    def test_remote_heartbeat_watchdog_runs_without_status_request(self):
        self.start();self.tick();m=Maintenance();m.session=self.s
        m.tick_probe(self.now-2.1)
        self.assertEqual(self.s.probe.result['phase'],'failed')
        self.assertIn('页面心跳',self.s.probe.result['error']);self.assert_measurement_only()

    def test_other_operations_refuse_while_probing_and_pending_is_invalidated(self):
        self.s.pending={'token':'old'};self.start();self.assertIsNone(self.s.pending)
        for f in (lambda:self.s.sample(14),lambda:self.s.apply('old'),lambda:self.s.undo(14),
                  lambda:self.s.hold(14),lambda:self.s.release(14),lambda:self.s.start_zero(True),self.start):
            with self.assertRaisesRegex(CalibrationError,'正在探测'):f()
        self.assertFalse(self.bus.writes)

    def test_lost_ack_after_goal_write_rolls_back_ram_and_torque(self):
        original=self.bus.write;failed=False
        def write(sid,addr,data):
            nonlocal failed
            result=original(sid,addr,data)
            if addr==42 and not failed:
                failed=True;raise OSError('lost ACK')
            return result
        self.start()
        with patch.object(self.bus,'write',side_effect=write):
            with self.assertRaisesRegex(OSError,'lost ACK'):self.tick()
        self.assert_measurement_only()

    def test_missing_write_without_ack_detected_by_readback(self):
        original=self.bus.write
        def drop(sid,addr,data):
            if addr==42:return None
            return original(sid,addr,data)
        self.start()
        with patch.object(self.bus,'write',side_effect=drop):
            with self.assertRaises(CalibrationError):self.tick()
        self.assert_measurement_only()

    def test_one_failed_read_during_motion_stops_every_joint(self):
        self.start();self.tick();original=self.bus.read;failed=False
        def read(sid,a,n):
            nonlocal failed
            if a==0 and not failed:failed=True;raise OSError('read failed')
            return original(sid,a,n)
        with patch.object(self.bus,'read',side_effect=read):
            with self.assertRaisesRegex(OSError,'read failed'):self.tick()
        self.assert_measurement_only()

    def test_physical_displacement_and_failed_return_abort(self):
        self.start();self.tick()
        self.bus.rows[13][56:58]=list((2100).to_bytes(2,'little'))
        with self.assertRaisesRegex(CalibrationError,'跟随偏差'):self.tick()
        self.assert_measurement_only()

    def test_wire_guard_rejects_eeprom_even_with_direct_call(self):
        bus=SerialBus.__new__(SerialBus);bus.measurement_only=True;bus.packet=Mock()
        for addr,data in ((31,b'\0\0'),(55,b'\0'),(55,b'\1')):
            with self.assertRaisesRegex(CalibrationError,'EEPROM'):bus.write(13,addr,data)
        bus.packet.assert_not_called()

    def test_return_jam_stops_instead_of_increasing_output(self):
        self.start()
        while self.s.probe.result['phase']!='returning':self.tick()
        self.bus.stops={13:(2094,2094)}
        with self.assertRaisesRegex(CalibrationError,'退回起点'):self.finish()
        self.assert_measurement_only()

    def test_external_eeprom_change_is_reported_but_never_rewritten(self):
        self.start();self.tick();self.bus.rows[13][31]=1
        with self.assertRaisesRegex(CalibrationError,'EEPROM'):self.tick()
        self.assertFalse(self.s.probe.result['eeprom_unchanged'])
        self.assertIsNotNone(self.s.fault)
        self.assertFalse(any(a in (31,55) for _,a,_ in self.bus.writes))
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))

    def test_manual_calibration_after_measurement_still_works(self):
        self.start();self.finish()
        p=self.s.sample(13);self.s.apply(p['token']);self.s.relax_all()
        self.assertIsNone(self.s.fault)

    def test_stale_motion_token_cannot_start_or_restart_probe(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(None,Path(d));app.request(dict(action='enter',supported=True))
            token=app.motion_token
            result=app.request(dict(action='probe',id=13,direction=1,confirmed=True,motion_token=token))
            self.assertEqual(result['limit_probe']['phase'],'arming')
            with self.assertRaisesRegex(CalibrationError,'取消'):
                app.request(dict(action='probe',id=13,direction=1,confirmed=True,motion_token=token))
            app.request(dict(action='relax'))
            with self.assertRaisesRegex(CalibrationError,'取消'):
                app.request(dict(action='probe',id=13,direction=1,confirmed=True,motion_token=result['motion_token']))
            self.assertFalse(any(a==42 for _,a,_ in app.backend.bus.writes))


class AgentLoopTests(unittest.TestCase):
    def test_real_stdio_loop_advances_without_requests_then_stops_on_heartbeat_loss(self):
        # Run the real scheduler against a fake bus: status handling itself must
        # not be what makes a live servo advance, and no HTTP watcher is present.
        script=textwrap.dedent('''
            from pathlib import Path
            from types import SimpleNamespace
            import agent
            from core import FakeBus,Session
            bus=FakeBus(follow_goals=True)
            m=agent.Maintenance()
            m.session=Session(bus,lambda *_:None,lambda *_:None)
            m.journal=SimpleNamespace(path=Path('/fake'))
            def close():
                if not m.closed:m.session.relax_all()
                m.closed=True
                return dict(closed=True,all_torque_off=not m.session.held)
            m.close=close
            agent.Maintenance=lambda *_:m
            agent.main()
        ''')
        p=subprocess.Popen([sys.executable,'-u','-c',script],cwd=Path(__file__).parent,
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def response():
            self.assertTrue(select.select([p.stdout],[],[],6)[0],'agent reply timed out')
            return json.loads(p.stdout.readline())
        def request(data):
            p.stdin.write(json.dumps(data)+'\n');p.stdin.flush();return response()
        try:
            self.assertTrue(response()['ready'])
            started=request(dict(action='probe',id=13,direction=1,confirmed=True))['result']['limit_probe']
            time.sleep(1)
            state=request(dict(action='status'))['result']
            self.assertGreater(state['limit_probe']['position_raw'],started['origin_raw'])
            time.sleep(3.8)
            state=request(dict(action='status'))['result']
            self.assertEqual(state['limit_probe']['phase'],'failed')
            self.assertIn('页面心跳',state['limit_probe']['error'])
            self.assertTrue(state['all_torque_off'])
            self.assertTrue(state['limit_probe']['eeprom_unchanged'])
            request(dict(action='finish'))
        finally:
            p.stdin.close()
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.kill();p.wait()
            stderr=p.stderr.read();p.stdout.close();p.stderr.close()
        self.assertEqual(p.returncode,0,stderr)


if __name__=='__main__':unittest.main()
