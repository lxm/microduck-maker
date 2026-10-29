import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock
import core
from core import Session, FakeBus, CalibrationError, IDS, BODY_IDS, word, signed, encode_offset, snapshot, transaction
from storage import Configs, Journal, encoded
from server import App, handler, ThreadingHTTPServer
import agent

class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.sleep=patch('core.time.sleep',return_value=None);self.sleep.start();self.addCleanup(self.sleep.stop)
        self.bus=FakeBus();self.events=[];self.persist=Mock()
        self.s=Session(self.bus,lambda *x:self.events.append(x),self.persist,dict(schema=1,id=34,mode=4,eeprom_offset_raw=0,closed_raw=2048,open_raw=1700))
    def calibrate(self,sid=13):
        p=self.s.sample(sid);self.s.apply(p['token']);return p
    def test_only_selected_offset_changes_and_no_motion(self):
        before=copy.deepcopy(self.bus.rows);self.calibrate()
        self.assertEqual(word(self.bus.rows[13],56),2048)
        self.assertEqual(signed(word(self.bus.rows[13],31)),7)
        self.assertTrue(all(self.bus.rows[s]==before[s] for s in IDS if s!=13))
        self.assertTrue(all(a in (31,55) for _,a,_ in self.bus.writes))
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))
    def test_signed_wrap_and_known_angle(self):
        self.bus.rows[10][31:33]=list(encode_offset(2040).to_bytes(2,'little'))
        self.bus.rows[10][56:58]=list((2100).to_bytes(2,'little'))
        self.s=Session(self.bus,lambda *_:None,self.persist)
        p=self.s.sample(10,10);self.s.apply(p['token'])
        self.assertEqual(word(self.bus.rows[10],56),round(2048-10*4096/360))
        self.assertLess(signed(p['new_offset_raw']),0)
    def test_invalid_ids_no_broadcast(self):
        for sid in (254,200,True,'13',None):
            with self.assertRaises(CalibrationError):self.s.sample(sid)
        self.assertEqual(self.bus.writes,[])
    def test_stability_and_after_sample_movement(self):
        original=self.bus.read;counter=0
        def moving(sid,a,n):
            nonlocal counter
            if a==56:counter+=1;return 0,(2000+counter).to_bytes(2,'little')
            return original(sid,a,n)
        with patch.object(self.bus,'read',side_effect=moving):
            with self.assertRaisesRegex(CalibrationError,'移动'):self.s.sample(13)
        p=self.s.sample(13);self.bus.rows[13][56:58]=list((2100).to_bytes(2,'little'))
        with self.assertRaisesRegex(CalibrationError,'移动'):self.s.apply(p['token'])
        self.assertEqual(self.bus.writes,[])
    def test_token_expiry_and_replay(self):
        p=self.s.sample(13);self.s.pending['time']-=31
        with self.assertRaisesRegex(CalibrationError,'30秒'):self.s.apply(p['token'])
        p=self.calibrate()
        with self.assertRaisesRegex(CalibrationError,'失效'):self.s.apply(p['token'])
    def test_external_register_change_refused(self):
        p=self.s.sample(13);self.bus.rows[20][50]=6
        with self.assertRaisesRegex(CalibrationError,'其他操作'):self.s.apply(p['token'])
        self.assertEqual(self.bus.writes,[])
    def test_lost_ack_rolls_back_offset_and_locks(self):
        initial=copy.deepcopy(self.bus.rows);original=self.bus.write;failed=False
        def lost(sid,a,data):
            nonlocal failed
            result=original(sid,a,data)
            if a==31 and not failed:failed=True;raise OSError('lost ACK after write')
            return result
        p=self.s.sample(13)
        with patch.object(self.bus,'write',side_effect=lost):
            with self.assertRaisesRegex(CalibrationError,'已恢复'):self.s.apply(p['token'])
        self.assertEqual(self.bus.rows,initial)
    def test_config_failure_rolls_back_eeprom(self):
        initial=copy.deepcopy(self.bus.rows);self.persist.side_effect=OSError('disk full')
        p=self.s.sample(13)
        with self.assertRaisesRegex(CalibrationError,'已恢复'):self.s.apply(p['token'])
        self.assertEqual(self.bus.rows,initial)
    def test_single_joint_hold_after_calibration_only_current_position(self):
        with self.assertRaisesRegex(CalibrationError,'先保存'):self.s.hold(13)
        self.calibrate();self.s.hold(13)
        self.assertEqual(self.s.held,{13});self.assertEqual(self.bus.rows[13][40],1)
        self.assertEqual(word(self.bus.rows[13],42),2048)
        self.assertEqual(word(self.bus.rows[13],48),300)
        self.assertEqual(self.bus.rows[13][50:52],[5,20])
        writes=self.bus.writes;goal=next(i for i,x in enumerate(writes) if x[1]==42)
        self.assertTrue(any(x[1]==48 for x in writes[:goal]))
        self.assertTrue(all(r[40]==0 for sid,r in self.bus.rows.items() if sid!=13))
    def test_can_calibrate_next_joint_while_previous_held(self):
        self.calibrate(13);self.s.hold(13);old=copy.deepcopy(self.bus.rows[13]);self.calibrate(14)
        self.assertEqual(self.bus.rows[13],old);self.s.hold(14)
        self.assertEqual(self.s.held,{13,14})
    def test_cannot_calibrate_or_undo_held_joint(self):
        self.calibrate();self.s.hold(13);count=len(self.bus.writes)
        with self.assertRaises(CalibrationError):self.s.sample(13)
        with self.assertRaises(CalibrationError):self.s.undo(13)
        self.assertEqual(len(self.bus.writes),count)
    def test_hold_rejects_moved_joint_no_new_goal(self):
        self.calibrate();self.bus.rows[13][56:58]=list((2100).to_bytes(2,'little'))
        with self.assertRaisesRegex(CalibrationError,'离开'):self.s.hold(13)
        self.assertFalse(any(a==42 for _,a,_ in self.bus.writes))
    def test_partial_hold_failure_turns_all_off(self):
        self.calibrate(13);self.s.hold(13);self.calibrate(14);original=self.bus.write;failed=False
        def fail(sid,a,data):
            nonlocal failed
            result=original(sid,a,data)
            if sid==14 and a==42 and not failed:failed=True;raise OSError('arming ACK lost')
            return result
        with patch.object(self.bus,'write',side_effect=fail):
            with self.assertRaises(OSError):self.s.hold(14)
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))
        self.assertEqual(self.s.held,set())
        self.assertTrue(all(r[48:52]==[232,3,32,40] for r in self.bus.rows.values()))
    def test_release_and_finish_never_restore_old_goal(self):
        self.calibrate();self.s.hold(13);self.bus.writes=[];self.s.release(13)
        self.assertFalse(any(a==42 for _,a,_ in self.bus.writes))
        self.assertEqual(self.bus.rows[13][48:52],[232,3,32,40]);self.s.undo(13)
        self.assertEqual(word(self.bus.rows[13],31),0)
        self.assertEqual(word(self.bus.rows[13],56),2055)
    def test_all_off_attempts_every_id_even_if_one_unreachable(self):
        original=self.bus.write
        def broken(sid,a,data):
            if sid==11 and a==40:raise OSError('bus fault')
            return original(sid,a,data)
        with patch.object(self.bus,'write',side_effect=broken):
            with self.assertRaisesRegex(CalibrationError,'切断主电源'):self.s.relax_all()
        self.assertTrue(any(sid==34 and a==40 for sid,a,_ in self.bus.writes))
        self.assertIsNotNone(self.s.fault)
    def test_held_following_error(self):
        self.calibrate();self.s.hold(13);self.bus.rows[13][56:58]=list((2100).to_bytes(2,'little'))
        with self.assertRaisesRegex(CalibrationError,'超过4'):self.s.refresh()
    def test_mouth_endpoints_and_undo(self):
        self.bus.rows[34][56:58]=list((2058).to_bytes(2,'little'));self.s.sample(34,endpoint='closed')
        self.bus.rows[34][56:58]=list((1700).to_bytes(2,'little'));p=self.s.sample(34,endpoint='open');self.s.apply(p['token'])
        self.assertEqual(self.s.mouth['open_raw'],1690);self.assertEqual(word(self.bus.rows[34],31),10)
        self.assertEqual(word(self.bus.rows[34],56),1690);self.s.hold(34);self.s.release(34);self.s.undo(34)
        self.assertEqual(self.s.mouth['open_raw'],1700);self.assertEqual(word(self.bus.rows[34],31),0)
    def test_invalid_hardware_snapshot_blocks(self):
        for addr,val in [(40,1),(33,0),(55,0),(65,8),(63,55),(62,59)]:
            b=FakeBus();b.rows[10][addr]=val
            with self.assertRaises(CalibrationError):snapshot(b)

    def test_all_fifteen_sequential_hold_and_final_off(self):
        for sid in BODY_IDS:
            self.calibrate(sid);self.s.hold(sid)
        self.bus.rows[34][56:58]=list((2048).to_bytes(2,'little'));self.s.sample(34,endpoint='closed')
        self.bus.rows[34][56:58]=list((1700).to_bytes(2,'little'));p=self.s.sample(34,endpoint='open');self.s.apply(p['token']);self.s.hold(34)
        self.assertEqual(self.s.held,set(IDS));self.assertEqual(len(self.s.completed),15)
        self.assertEqual(len(self.s.refresh()['servos']),15)
        self.s.relax_all();self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))
        self.assertTrue(all(word(self.bus.rows[s],56)==2048 for s in BODY_IDS))
        self.assertEqual(word(self.bus.rows[34],56),1700)

class ZeroInspectionTests(unittest.TestCase):
    def setUp(self):
        self.sleep=patch('core.time.sleep',return_value=None);self.sleep.start();self.addCleanup(self.sleep.stop)
        self.clock=100.0
        self.timer=patch('core.time.monotonic',side_effect=lambda:self.clock);self.timer.start();self.addCleanup(self.timer.stop)
        self.bus=FakeBus(follow_goals=True)
        for sid in IDS:
            self.bus.rows[sid][56:58]=list((1900 if sid==34 else 2148).to_bytes(2,'little'))
            self.bus.rows[sid][31:33]=list(encode_offset(sid*10).to_bytes(2,'little'))
        self.mouth=dict(schema=1,id=34,mode=4,eeprom_offset_raw=340,closed_raw=2048,open_raw=1700)
        self.events=[];self.persist=Mock()
        self.s=Session(self.bus,lambda *x:self.events.append(x),self.persist,self.mouth)
    def tick(self):
        self.clock+=.25
        return self.s.advance_zero()
    def arm(self):
        self.s.start_zero(True)
        for _ in BODY_IDS:self.tick()
        self.assertEqual(self.s.zero_check['phase'],'moving')
    def test_confirm_required_no_goals_and_no_all_recalibration_requirement(self):
        for val in (False,None,1,'true'):
            with self.assertRaises(CalibrationError):self.s.start_zero(val)
        self.assertFalse(self.bus.writes)
        self.assertFalse(self.s.completed)
        self.s.start_zero(True)
        self.assertFalse(self.bus.writes,'queueing must never send a goal')
    def test_current_position_preload_then_bounded_zero_no_eeprom_or_double_offset(self):
        initial=copy.deepcopy(self.bus.rows);self.arm()
        for sid in BODY_IDS:
            goals=[word(data,0) for s,a,data in self.bus.writes if s==sid and a==42]
            self.assertEqual(goals,[word(initial[sid],56)])
        while self.s.zero_running():self.tick()
        self.assertEqual(self.s.zero_check['phase'],'holding')
        self.assertEqual(self.s.held,set(BODY_IDS))
        for sid in BODY_IDS:
            row=self.bus.rows[sid]
            self.assertEqual(word(row,42),2048)
            self.assertEqual(word(row,56),2048)
            self.assertEqual(word(row,31),word(initial[sid],31))
            goals=[word(data,0) for s,a,data in self.bus.writes if s==sid and a==42]
            self.assertTrue(all(abs(b-a)<=12 for a,b in zip(goals,goals[1:])))
            self.assertEqual(word(row,48),300)
        self.assertEqual(self.bus.rows[34],initial[34])
        self.assertFalse(any(sid==34 for sid,_,_ in self.bus.writes))
        self.assertFalse(any(a in (31,55) for _,a,_ in self.bus.writes))
        self.persist.assert_not_called()
        self.s.relax_all()
        self.assertTrue(all(r[40]==0 and r[48:52]==[232,3,32,40] for r in self.bus.rows.values()))
    def test_off_during_preload_or_motion_never_resumes_on_status(self):
        for count in (1,20):
            self.s.start_zero(True)
            for _ in range(count):self.tick()
            self.s.relax_all();self.bus.writes=[]
            for _ in range(3):self.tick()
            self.assertEqual(self.s.zero_check['phase'],'cancelled')
            self.assertFalse(self.bus.writes)
            self.assertEqual(self.s.held,set())
    def test_stuck_joint_aborts_and_turns_every_servo_off(self):
        self.bus.follow_goals=False;self.arm()
        with self.assertRaisesRegex(CalibrationError,'超过4'):
            for _ in range(10):self.tick()
        self.assertEqual(self.s.zero_check['phase'],'failed')
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))
    def test_goal_applied_but_ack_lost_aborts_no_retry(self):
        self.arm();original=self.bus.write;failed=False
        def lost(sid,a,data):
            nonlocal failed
            value=original(sid,a,data)
            if sid==12 and a==42 and not failed:failed=True;raise OSError('lost goal ACK')
            return value
        with patch.object(self.bus,'write',side_effect=lost):
            with self.assertRaisesRegex(OSError,'lost goal'):self.tick()
        self.assertEqual(self.s.zero_check['phase'],'failed')
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))
    def test_missing_write_with_suppressed_ack_is_not_success(self):
        self.arm();original=self.bus.write
        def dropped(sid,a,data):
            return None if sid==12 and a==42 else original(sid,a,data)
        with patch.object(self.bus,'write',side_effect=dropped):
            with self.assertRaisesRegex(CalibrationError,'配置被其他操作改变'):self.tick()
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))
    def test_delay_never_catches_up_with_large_goal_jump(self):
        self.arm();before=word(self.bus.rows[13],42);self.clock+=8;self.tick()
        self.assertLessEqual(abs(word(self.bus.rows[13],42)-before),12)
    def test_timeout_aborts_even_with_valid_feedback(self):
        self.arm();self.clock+=181
        with self.assertRaisesRegex(CalibrationError,'超时'):self.tick()
        self.assertTrue(all(r[40]==0 for r in self.bus.rows.values()))
    def test_body_out_of_range_refuses_before_writes(self):
        self.bus.rows[11][56:58]=list((3000).to_bytes(2,'little'))
        with self.assertRaisesRegex(CalibrationError,'ID11'):self.s.start_zero(True)
        self.assertFalse(self.bus.writes)

    def test_missing_or_invalid_mouth_is_excluded_from_motion_and_error(self):
        for mouth in (None,{},dict(eeprom_offset_raw=12,closed_raw=100,open_raw=4000)):
            with self.subTest(mouth=mouth):
                self.bus=FakeBus(follow_goals=True)
                self.bus.rows[34][56:58]=list((50).to_bytes(2,'little'))
                original=copy.deepcopy(self.bus.rows[34])
                self.s=Session(self.bus,lambda *_:None,Mock(),mouth)
                self.arm()
                for _ in range(300):
                    self.tick()
                    if not self.s.zero_running():break
                self.assertEqual(self.s.zero_check['phase'],'holding')
                self.assertEqual(self.s.zero_check['ids'],BODY_IDS)
                self.assertEqual(set(self.s.zero_check['targets']),set(map(str,BODY_IDS)))
                self.assertEqual(self.s.held,set(BODY_IDS))
                self.assertEqual(self.s.zero_check['max_error_deg'],0)
                self.assertEqual(self.bus.rows[34],original)
                self.assertFalse(any(sid==34 for sid,_,_ in self.bus.writes))

    def test_zero_goal_rejects_mouth_before_any_write(self):
        with self.assertRaisesRegex(CalibrationError,"不包括嘴部"):self.s.zero_goal(34,2048)
        self.assertFalse(self.bus.writes)

    def test_previous_mouth_hold_is_released_without_zero_goal_or_enable(self):
        self.s.held.add(34);self.bus.rows[34][40]=1;self.s.current['34'][40]=1
        self.s.start_zero(True)
        self.assertNotIn(34,self.s.held)
        self.assertEqual(self.bus.rows[34][40],0)
        self.assertFalse(any(sid==34 and (addr==42 or (addr==40 and data==[1])) for sid,addr,data in self.bus.writes))
        self.assertEqual(self.s.zero_check['ids'],BODY_IDS)

    def test_http_action_in_demo_without_mouth_calibration_targets_body_only(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(None,Path(d));app.request(dict(action='enter',supported=True))
            app.backend.session.mouth=None
            result=app.request(dict(action='zero',confirmed=True,motion_token=app.motion_token))
            self.assertEqual(result['zero_inspection_ids'],BODY_IDS)
            self.assertEqual(result['zero_check']['ids'],BODY_IDS)
            for _ in range(200):
                self.clock+=.25;result=app.request(dict(action='status'))
                if result['zero_check']['phase']=='holding':break
            self.assertEqual(result['held'],BODY_IDS)
            self.assertFalse(any(sid==34 for sid,_,_ in app.backend.bus.writes))

    def test_single_joint_writes_blocked_during_motion(self):
        self.arm();before=len(self.bus.writes)
        for action in (lambda:self.s.sample(13),lambda:self.s.apply('x'),lambda:self.s.undo(13),lambda:self.s.hold(13),lambda:self.s.release(13)):
            with self.assertRaisesRegex(CalibrationError,'正在归零'):action()
        self.assertEqual(len(self.bus.writes),before)
    def test_heartbeat_loss_cancels_queued_zero_in_demo(self):
        with tempfile.TemporaryDirectory() as d:
            a=App(None,Path(d));a.request(dict(action='enter',supported=True));a.request(dict(action='zero',confirmed=True,motion_token=a.motion_token))
            a.request(dict(action='status'));self.assertTrue(a.backend.session.held)
            a.last=self.clock-11;a.check_idle()
            self.assertTrue(a.result['all_torque_off']);self.assertFalse(a.backend.session.held)

class StorageTests(unittest.TestCase):
    def test_file_failure_rolls_back_all_files_and_offset(self):
        with tempfile.TemporaryDirectory() as d,patch('core.time.sleep',return_value=None):
            p=Path(d);config=p/'config';config.mkdir();journal=Journal(p/'backup');bus=FakeBus()
            body=dict(schema=1,servos=[dict(id=s,offset_raw=0) for s in BODY_IDS]);mouth=dict(schema=1,id=34,mode=4,eeprom_offset_raw=0,closed_raw=2048,open_raw=1700)
            (config/'hd1910-body-calibration.json').write_bytes(encoded(body));(config/'hd1910-mouth.json').write_bytes(encoded(mouth))
            c=Configs(config,journal);c.validate(snapshot(bus));s=Session(bus,journal.save,c.commit,mouth);pending=s.sample(13)
            import storage
            original=storage.atomic;failed=False
            def fail(path,data):
                nonlocal failed
                original(path,data)
                if Path(path).name=='hd1910-body-calibration.json' and not failed:failed=True;raise OSError('failed after replace')
            with patch('storage.atomic',side_effect=fail):
                with self.assertRaisesRegex(CalibrationError,'已恢复'):s.apply(pending['token'])
            self.assertEqual(json.loads((config/'hd1910-body-calibration.json').read_text()),body)
            self.assertEqual(word(bus.rows[13],31),0)
            self.assertTrue((journal.path/'registers-before.json').exists())
    def test_missing_body_file_is_not_created(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);config=p/'config';config.mkdir();journal=Journal(p/'backup');b=FakeBus()
            mouth=dict(schema=1,id=34,mode=4,eeprom_offset_raw=0,closed_raw=2048,open_raw=1700)
            (config/'hd1910-mouth.json').write_bytes(encoded(mouth));c=Configs(config,journal);c.validate(snapshot(b));c.commit(snapshot(b),mouth)
            self.assertFalse((config/'hd1910-body-calibration.json').exists())

class LifecycleTests(unittest.TestCase):
    def test_missing_daemon_status_allows_only_direct_read_entry(self):
        with patch('agent.off_gate',side_effect=FileNotFoundError()),patch.object(agent.HARDWARE.__class__,'exists',return_value=False):
            self.assertTrue(agent.entry_gate()['direct_snapshot_required'])
        with patch('agent.off_gate',side_effect=CalibrationError('torque on')):
            with self.assertRaises(CalibrationError):agent.entry_gate()
    def test_disconnect_relaxes_before_restart_and_inputs_after_gate(self):
        m=agent.Maintenance();m.session=Mock();m.bus=Mock();m.bus_lock=Mock();m.daemon_stopped=True;m.stopped=['codexpad-s10','robotd'];events=[]
        m.session.relax_all.side_effect=lambda:events.append('off')
        m.bus.close.side_effect=lambda:events.append('close-bus')
        m.bus_lock.close.side_effect=lambda:events.append('unlock-bus')
        with patch('agent.command',side_effect=lambda *a,**_:events.append(a)),patch('agent.off_gate',side_effect=lambda:events.append('gate')):
            result=m.close()
        self.assertEqual(events,['off','close-bus','unlock-bus',('systemctl','start','robotd'),'gate',('systemctl','start','codexpad-s10')])
        self.assertTrue(result['services_restored'])
    def test_uncertain_off_never_restarts_daemon_or_input(self):
        m=agent.Maintenance();m.session=Mock();m.bus=Mock();m.daemon_stopped=True;m.stopped=['robotd','codexpad-s10'];m.session.relax_all.side_effect=OSError('no reply')
        with patch('agent.command') as cmd:result=m.close()
        cmd.assert_not_called();self.assertFalse(result['all_torque_off']);self.assertFalse(result['services_restored'])
    def test_restart_gate_failure_is_not_reported_as_confirmed_off(self):
        m=agent.Maintenance();m.session=Mock();m.bus=Mock();m.daemon_stopped=True;m.stopped=['robotd','codexpad-s10']
        with patch('agent.command') as cmd,patch('agent.off_gate',side_effect=CalibrationError('not off')),patch('agent.time.monotonic',side_effect=[0,9]):
            result=m.close()
        self.assertFalse(result['all_torque_off']);self.assertFalse(result['services_restored'])
        self.assertEqual(cmd.call_args_list, [unittest.mock.call('systemctl','start','robotd')])
    def test_page_heartbeat_timeout_relaxes_demo(self):
        with tempfile.TemporaryDirectory() as d,patch('core.time.sleep',return_value=None):
            a=App(None,Path(d));a.request(dict(action='enter',supported=True));p=a.request(dict(action='sample',id=13))['pending'];a.request(dict(action='apply',token=p['token']));a.request(dict(action='hold',id=13,clear=True,motion_token=a.motion_token))
            a.last=time.monotonic()-11;a.check_idle()
            self.assertTrue(a.expired);self.assertTrue(a.result['all_torque_off']);self.assertEqual(a.backend.session.held,set())
            with self.assertRaises(CalibrationError):a.request(dict(action='hold',id=13,clear=True))
    def test_wire_guard_blocks_broadcast_and_unlisted_write(self):
        bus=agent.SerialBus.__new__(agent.SerialBus);bus.ser=Mock()
        for sid,addr,data in [(254,40,b'\0'),(13,33,b'\0'),(13,31,b'\0')]:
            with self.assertRaises(CalibrationError):bus.write(sid,addr,data)
        bus.ser.write.assert_not_called()

    def test_delayed_enable_cannot_undo_a_later_stop(self):
        with tempfile.TemporaryDirectory() as d,patch('core.time.sleep',return_value=None):
            a=App(None,Path(d));a.request(dict(action='enter',supported=True));old=a.motion_token
            a.request(dict(action='relax'))
            for action in ('zero','hold'):
                with self.assertRaisesRegex(CalibrationError,'取消'):
                    a.request(dict(action=action,confirmed=True,id=13,clear=True,motion_token=old))
            a.request(dict(action='status'))
            self.assertFalse(a.backend.session.held)
            self.assertIsNone(a.backend.session.zero_check)
            a.request(dict(action='zero',confirmed=True,motion_token=a.motion_token))
            self.assertEqual(a.backend.session.zero_check['phase'],'arming')

class HttpTests(unittest.TestCase):
    def test_host_origin_token_and_path_protection(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(None,Path(d));server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                c=http.client.HTTPConnection('127.0.0.1',server.server_port)
                c.request('GET','/api/info');r=c.getresponse();info=json.loads(r.read());self.assertEqual(r.status,200)
                c.request('GET','/%2e%2e/core.py');r=c.getresponse();r.read();self.assertEqual(r.status,404)
                c.request('GET','/api/info',headers={'Host':'attacker.example'});r=c.getresponse();r.read();self.assertEqual(r.status,403)
                c.request('POST','/api/action',body='{}',headers={'Content-Type':'application/json','X-CSRF-Token':info['csrf'],'Origin':'https://attacker.example'});r=c.getresponse();r.read();self.assertEqual(r.status,403)
                c.close()
            finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
