import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fd_mac import Workbench, make_handler


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.w = Workbench(self.temp.name)
        self.w.call('connect', dict(port='DEMO'))
        self.w.call('scan', dict(start=0, end=35))

    def test_connect_scan_are_read_only(self):
        self.assertEqual(self.w.ids, [1, 10, 20, 30])
        session = self.w.call('ports', {})
        self.assertTrue(session['connected'])
        self.assertEqual(session['port'], 'DEMO')
        self.assertEqual(session['ids'], self.w.ids)
        for sid in self.w.ids:
            self.assertEqual(self.w.read(sid, 40, 1), 0)
            self.assertEqual(self.w.read(sid, 42, 2), 2300)

    def test_snapshot_respects_hd_register_end(self):
        original = self.w.bus.read
        def bounded_read(sid, addr, n):
            self.assertLessEqual(addr + n, 87)
            return original(sid, addr, n)
        with patch.object(self.w.bus, 'read', side_effect=bounded_read):
            snapshot = self.w.call('dump', dict(id=1))
        self.assertEqual(len(snapshot['raw']), 87)
        self.assertEqual(snapshot['rows'][-1]['addr'], 86)

    def test_enable_overwrites_stale_target_before_torque(self):
        self.w.bus.write_u16(1, 42, 999)
        self.w.call('torque', dict(id=1, on=1))
        self.assertEqual(self.w.read(1, 42, 2), 2300)
        self.assertEqual(self.w.read(1, 40, 1), 1)

    def test_motion_requires_torque_and_large_move_confirmation(self):
        with self.assertRaisesRegex(ValueError, '扭矩'):
            self.w.call('move', dict(id=1, position=2311))
        self.w.call('torque', dict(id=1, on=1))
        with self.assertRaisesRegex(ValueError, '10°'):
            self.w.call('move', dict(id=1, position=3000))
        self.assertEqual(self.w.read(1, 42, 2), 2300)
        self.w.call('move', dict(id=1, position=2311))
        self.assertEqual(self.w.read(1, 42, 2), 2311)

    def test_calibration_backup_before_mutation_and_torque_off(self):
        result = self.w.call('calibrate', dict(id=1))
        backup = json.loads(Path(result['backup']).read_text())
        self.assertEqual(backup['raw'][56:58], list((2300).to_bytes(2, 'little')))
        self.assertTrue(backup['demo'])
        self.assertEqual(result['after']['position'], 2048)
        self.assertEqual(self.w.read(1, 40, 1), 0)
        self.assertEqual(self.w.read(1, 55, 1), 1)
        self.assertEqual(self.w.read(1, 42, 2), 2048)

    def test_backup_failure_prevents_calibration(self):
        with patch.object(self.w, 'backup', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.w.call('calibrate', dict(id=1))
        self.assertEqual(self.w.read(1, 56, 2), 2300)

    def test_eeprom_requires_confirmation_and_verifies(self):
        with self.assertRaisesRegex(ValueError, '确认'):
            self.w.call('write', dict(id=1, addr=21, value=32))
        result = self.w.call('write', dict(id=1, addr=21, value=32, confirm=True))
        self.assertTrue(Path(result['backup']).exists())
        self.assertEqual(self.w.read(1, 21, 1), 32)
        self.assertEqual(self.w.read(1, 55, 1), 1)

    def test_eeprom_write_failure_relocks(self):
        original = self.w.put
        def fail(sid, addr, size, value):
            if addr == 21:
                raise ValueError('write failed')
            return original(sid, addr, size, value)
        with patch.object(self.w, 'put', side_effect=fail):
            with self.assertRaisesRegex(ValueError, 'write failed'):
                self.w.call('write', dict(id=1, addr=21, value=32, confirm=True))
        self.assertEqual(self.w.read(1, 55, 1), 1)

    def test_dedicated_registers_cannot_bypass_workflows(self):
        for addr in (5, 6, 7, 8, 31, 40, 42, 44, 55, 56, 0):
            with self.subTest(addr=addr), self.assertRaises(ValueError):
                self.w.call('write', dict(id=1, addr=addr, value=1, confirm=True))

    def test_set_id_refuses_multiple_devices(self):
        with self.assertRaisesRegex(ValueError, '一颗'):
            self.w.call('set_id', dict(id=1, new=2))
        self.assertEqual(self.w.read(1, 5, 1), 1)

    def test_set_id_single_device_and_relock(self):
        self.w.bus.regs = {1: self.w.bus.regs[1]}
        result = self.w.call('set_id', dict(id=1, new=2))
        self.assertEqual(result['id'], 2)
        self.assertEqual(self.w.ids, [2])
        self.assertEqual(self.w.read(2, 55, 1), 1)

    def test_invalid_id_and_non_integer_rejected(self):
        for sid in (-1, 254, 1.5, True, 'abc'):
            with self.subTest(sid=sid), self.assertRaises((ValueError, TypeError)):
                self.w.call('state', dict(id=sid))

    def test_unload_and_disconnect(self):
        self.w.call('torque', dict(id=1, on=1))
        self.assertFalse(self.w.call('unload', {})['failures'])
        self.assertEqual(self.w.read(1, 40, 1), 0)
        self.w.call('disconnect', {})
        with self.assertRaisesRegex(ValueError, '打开'):
            self.w.call('state', dict(id=1))

    def test_non_position_mode_refuses_enable(self):
        self.w.bus.regs[1][33] = 3
        with self.assertRaisesRegex(ValueError, '位置模式'):
            self.w.call('torque', dict(id=1, on=1))
        self.assertEqual(self.w.read(1, 40, 1), 0)

    def test_unknown_model_read_only_and_can_unload(self):
        self.w.bus.regs[1][3:5] = bytes(2)
        self.assertIn('state', self.w.call('state', dict(id=1)))
        with self.assertRaisesRegex(ValueError, '型号'):
            self.w.call('calibrate', dict(id=1))
        self.w.call('torque', dict(id=1, on=0))

    def test_hd_pid_runtime_fields(self):
        rows = self.w.call('dump', dict(id=1))['rows']
        self.assertTrue({50, 51, 52}.issubset({r['addr'] for r in rows}))
        self.w.call('write', dict(id=1, addr=50, value=32))
        self.assertEqual(self.w.read(1, 50, 1), 32)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.w = Workbench(self.temp.name)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.w, 'test-token'))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, args, token='test-token'):
        req = urllib.request.Request(self.url+'/api', data=json.dumps(args).encode(),
              headers={'Content-Type':'application/json','X-FD-Token':token})
        return json.load(urllib.request.urlopen(req))

    def test_http_demo_flow_and_token(self):
        with urllib.request.urlopen(self.url) as res:
            self.assertIn(b"token='test-token'", res.read())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(dict(op='connect', port='DEMO'), token='wrong')
        self.assertEqual(caught.exception.code, 403)
        self.assertIsNone(self.w.bus)
        self.assertTrue(self.request(dict(op='connect', port='DEMO'))['demo'])
        self.assertEqual(self.request(dict(op='scan', start=1, end=1))['ids'], [1])
        self.assertEqual(self.request(dict(op='state', id=1))['state']['pos'], 2300)
        self.assertEqual(self.request(dict(op='calibrate', id=1))['after']['position'], 2048)

    def test_static_assets_are_served_without_exposing_local_files(self):
        for path, mime in (('/app.css', 'text/css'), ('/app.js', 'text/javascript')):
            with urllib.request.urlopen(self.url + path) as response:
                self.assertEqual(response.headers.get_content_type(), mime)
                self.assertGreater(len(response.read()), 100)
        for path in ('/fd_mac.py', '/requirements.txt', '/licenses/SOURCE-LICENSE.txt', '/../fd_mac.py'):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.url + path)
            self.assertEqual(caught.exception.code, 404)
        req = urllib.request.Request(self.url + '/app.js', headers={'Host': 'untrusted.example'})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req)
        self.assertEqual(caught.exception.code, 403)

    def test_untrusted_host_is_rejected(self):
        req = urllib.request.Request(self.url, headers={'Host':'untrusted.example'})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req)
        self.assertEqual(caught.exception.code, 403)


if __name__ == '__main__':
    unittest.main()
