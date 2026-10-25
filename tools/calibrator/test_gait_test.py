import contextlib
import copy
import io
import unittest
from unittest.mock import patch
import gait_test as g

class Tests(unittest.TestCase):
    def setUp(self):
        p=patch('core.time.sleep',lambda _:None);p.start();self.addCleanup(p.stop)
    def run_demo(self,m=None,stop=lambda:False):
        self.m=m or g.DemoMaintenance();self.before=copy.deepcopy(self.m.bus.rows);self.events=[]
        return g.inspect(self.m,stop,self.events.append,lift=12,cycles=1,countdown=3,clock=g.Clock(True))
    def test_success_scope_steps_and_restoration(self):
        r=self.run_demo();self.assertIsNone(r['error']);self.assertTrue(r['torque_off_verified'])
        self.assertEqual({sid for sid,a,_ in self.m.bus.writes if a==42},set(g.LEGS))
        self.assertFalse(any(a in (31,55) for _,a,_ in self.m.bus.writes))
        for sid,row in self.m.bus.rows.items():
            self.assertEqual(row[40],0);self.assertEqual(row[:40],self.before[sid][:40]);self.assertEqual(row[48:53],self.before[sid][48:53])
            goals=[int.from_bytes(bytes(d),'little') for s,a,d in self.m.bus.writes if s==sid and a==42]
            if len(goals)>1:self.assertLessEqual(max(abs(a-b) for a,b in zip(goals,goals[1:])),4)
        self.assertTrue(any(e.get('phase')=='full_1_left_lift' for e in self.events))
    def test_original_lower_caps_and_pid_preserved(self):
        m=g.DemoMaintenance()
        for sid in g.LEGS:m.bus.rows[sid][48:50]=list((150).to_bytes(2,'little'))
        self.assertIsNone(self.run_demo(m)['error'])
        for e in self.events:
            for row in e.get('rows',[]):
                if row['enabled']:
                    self.assertEqual(row['limit_raw'],150);self.assertEqual([row['p'],row['d']],self.before[row['id']][50:52])
    def test_cancel_during_activation_cleans_all(self):
        m=g.DemoMaintenance();r=self.run_demo(m,lambda:bool(m.bus.writes))
        self.assertIsNotNone(r['error']);self.assertTrue(r['torque_off_verified']);self.assertTrue(all(x[40]==0 for x in m.bus.rows.values()))
        self.assertFalse(any(a==42 for _,a,_ in m.bus.writes))
    def test_runtime_read_failure_not_retried(self):
        m=g.DemoMaintenance();old=m.bus.read;failures=[]
        def fail(sid,a,n):
            if sid==31 and a==40 and any(x[40] for x in m.bus.rows.values()):failures.append(1);return 0,b''
            return old(sid,a,n)
        m.bus.read=fail;r=self.run_demo(m)
        self.assertIsNotNone(r['error']);self.assertEqual(len(failures),1);self.assertTrue(r['torque_off_verified'])
    def test_undervoltage_stops(self):
        m=g.DemoMaintenance();old=m.bus.write
        def voltage(sid,a,d):
            out=old(sid,a,d)
            if a==42:m.bus.rows[sid][62]=64
            return out
        m.bus.write=voltage;r=self.run_demo(m)
        self.assertIn('6.4V',r['error']);self.assertTrue(r['torque_off_verified'])
    def test_stuck_joint_stops(self):
        m=g.DemoMaintenance();m.bus.follow_goals=False;r=self.run_demo(m)
        self.assertIsNotNone(r['error']);self.assertTrue(r['torque_off_verified'])
    def test_unapplied_goal_write_caught(self):
        m=g.DemoMaintenance();old=m.bus.write
        m.bus.write=lambda sid,a,d:0 if a==42 else old(sid,a,d)
        r=self.run_demo(m);self.assertIn('goal readback',r['error']);self.assertTrue(r['torque_off_verified'])
        self.assertFalse(any(a==40 and list(d)==[1] for _,a,d in m.bus.writes))
    def test_lost_ack_after_applied_goal_uses_readback(self):
        m=g.DemoMaintenance();old=m.bus.write
        def no_ack(sid,a,d):
            value=old(sid,a,d)
            return None if a==42 else value
        m.bus.write=no_ack;r=self.run_demo(m)
        self.assertIsNone(r['error']);self.assertTrue(r['torque_off_verified'])
    def test_lost_ack_without_applied_goal_never_enables_old_goal(self):
        m=g.DemoMaintenance();old=m.bus.write
        m.bus.write=lambda sid,a,d:None if a==42 else old(sid,a,d)
        r=self.run_demo(m);self.assertIn('goal readback',r['error'])
        self.assertFalse(any(a==40 and list(d)==[1] for _,a,d in m.bus.writes))
        self.assertTrue(r['torque_off_verified'])
    def test_failed_entry_not_claimed_torque_off(self):
        m=g.DemoMaintenance();m.bus.rows[10][40]=1;r=self.run_demo(m)
        self.assertFalse(r['entered']);self.assertFalse(r['torque_off_verified'])
    def test_mirror_and_ranges(self):
        for lift in (4,6,12,15):
            neutral,p=g.poses(lift);right,left=p[0][1],p[2][1]
            for a,b in ((12,22),(13,23),(14,24)):self.assertEqual(right[a]+left[b],4096)
            for sid in (10,11,20,21):self.assertEqual(right[sid],neutral[sid])
    def test_invalid_cli_rejected(self):
        for argv in (['--lift-deg','nan'],['--speed','inf'],['--torque-limit','1000'],['--ssh','radxa@host'],['--ssh','bad;host','--supported'],['--demo','--worker','--supported']):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):g.parse(argv)
    def test_logging_failure_cannot_skip_torque_off(self):
        m=g.DemoMaintenance()
        def broken(_):raise BrokenPipeError('disconnected')
        r=g.inspect(m,lambda:False,broken,clock=g.Clock(True))
        self.assertIn('logging/output',r['error']);self.assertTrue(r['torque_off_verified'])
        self.assertTrue(all(row[40]==0 for row in m.bus.rows.values()))
    def test_cancel_mid_gait_no_more_motion(self):
        m=g.DemoMaintenance();cancel=[False];count=[0]
        def event(e):
            if e.get('event')=='sample' and e.get('phase')=='half_1_right_lift':
                cancel[0]=True;count[0]=sum(a==42 for _,a,_ in m.bus.writes)
        r=g.inspect(m,lambda:cancel[0],event,cycles=1,countdown=3,clock=g.Clock(True))
        self.assertIsNotNone(r['error']);self.assertTrue(r['torque_off_verified'])
        self.assertEqual(sum(a==42 for _,a,_ in m.bus.writes),count[0])
    def test_keyboard_eof_does_not_request_stop(self):
        stop=g.StopRequest();g.keyboard_loop(io.StringIO(''),stop)
        self.assertFalse(stop.is_set())
    def test_keyboard_enter_records_first_source(self):
        stop=g.StopRequest();g.keyboard_loop(io.StringIO('\n'),stop)
        stop.request('local_signal:SIGINT')
        self.assertEqual(stop.reason,'keyboard:Enter')
    def test_cancel_before_entry_does_not_close_or_claim_torque(self):
        m=g.DemoMaintenance();m.enter=lambda: self.fail('must not enter')
        m.close=lambda: self.fail('must not claim cleanup without entry')
        r=g.inspect(m,lambda:'controller:STOP',lambda _:None,clock=g.Clock(True))
        self.assertFalse(r['entered']);self.assertFalse(r['entry_attempted'])
        self.assertEqual(r['error'],'controller:STOP');self.assertIsNone(r['cleanup']['all_torque_off'])
        self.assertFalse(r['torque_off_verified'])
    def test_worker_startup_stop_and_eof_never_enter_hardware(self):
        import subprocess,sys,json
        for data,reason in [('STOP\n','controller:STOP'),('','controller:stdin closed')]:
            r=subprocess.run([sys.executable,g.__file__,'--worker','--supported'],input=data,text=True,capture_output=True,timeout=5)
            self.assertEqual(r.returncode,1,r.stderr)
            events=[json.loads(line) for line in r.stdout.splitlines()]
            final=events[-1];self.assertEqual(final['error'],reason)
            self.assertFalse(final['entry_attempted']);self.assertIsNone(final['cleanup']['all_torque_off'])
            self.assertIn('entry_cancelled',[e['event'] for e in events])
    def test_real_tty_discards_old_enter_and_accepts_new_enter(self):
        import os,pty,select,threading
        master,slave=pty.openpty();stream=os.fdopen(os.dup(slave),'r')
        try:
            os.write(master,b'\n');self.assertTrue(select.select([slave],[],[],1)[0])
            self.assertTrue(g.prepare_keyboard(stream))
            self.assertFalse(select.select([slave],[],[],0)[0])
            stop=g.StopRequest();thread=threading.Thread(target=g.keyboard_loop,args=(stream,stop),daemon=True);thread.start()
            self.assertFalse(stop.is_set());os.write(master,b'\n');thread.join(1)
            self.assertFalse(thread.is_alive());self.assertEqual(stop.reason,'keyboard:Enter')
        finally:
            os.close(master);os.close(slave);stream.close()
    def test_onboard_enter_ignored_and_terminal_eof_stops(self):
        stop=g.StopRequest();g.keyboard_loop(io.StringIO('\n\n'),stop,enter_stops=False,eof_stops=True)
        self.assertEqual(stop.reason,'terminal:EOF')
    def test_onboard_requires_root_and_tty(self):
        class Tty:
            def isatty(self):return True
        with self.assertRaises(g.CalibrationError):g.validate_onboard(io.StringIO(),0)
        with self.assertRaises(g.CalibrationError):g.validate_onboard(Tty(),1000)
        g.validate_onboard(Tty(),0)
    def test_onboard_supported_and_mode_validation(self):
        self.assertTrue(g.parse(['--onboard','--supported']).onboard)
        for argv in (['--onboard'],['--onboard','--demo'],['--onboard','--worker','--supported']):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):g.parse(argv)
    def test_onboard_no_tty_subprocess_never_touches_robot(self):
        import subprocess,sys
        r=subprocess.run([sys.executable,g.__file__,'--onboard','--supported'],input='',text=True,capture_output=True,timeout=5)
        self.assertEqual(r.returncode,2);self.assertIn('交互终端',r.stderr)
    def test_cleanup_failure_visible(self):
        m=g.DemoMaintenance();old=m.close
        def failed():old();return dict(all_torque_off=False,services_restored=False,error='failure')
        m.close=failed;r=self.run_demo(m)
        self.assertFalse(r['torque_off_verified']);self.assertFalse(r['cleanup']['services_restored'])

if __name__=='__main__':unittest.main()
