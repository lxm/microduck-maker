"""Progress must be observable before SSH exits, including failure and timeout."""
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock

from core import CalibrationError
from server import App, Remote, SetupLog, ThreadingHTTPServer, handler
from storage import Journal
import agent


class SetupLogTests(unittest.TestCase):
    def test_streams_before_process_exits_and_keeps_json_separate(self):
        with tempfile.TemporaryDirectory() as d:
            remote=Remote('user@robot',Journal(Path(d)/'session'))
            gate=Path(d)/'continue';seen=threading.Event();out=[]
            append=remote.setup_log.append
            def record(line):
                append(line)
                if 'installing' in line:seen.set()
            remote.setup_log.append=record
            code="import pathlib,time,sys;print('installing <package>',file=sys.stderr,flush=True)\nwhile not pathlib.Path(sys.argv[1]).exists():time.sleep(.01)\nprint('{\"ok\":true,\"ready\":true}',flush=True)"
            thread=threading.Thread(target=lambda:out.append(remote.setup_command([sys.executable,'-u','-c',code,str(gate)],5)))
            thread.start()
            try:
                self.assertTrue(seen.wait(3),'progress must arrive while SSH is still running')
                self.assertTrue(thread.is_alive());self.assertFalse(out)
                self.assertIn('<package>',remote.setup_log.snapshot()['lines'][0])
            finally:
                gate.touch();thread.join(5)
            self.assertFalse(thread.is_alive());self.assertEqual(out,[{'ok':True,'ready':True}])
            self.assertEqual(len(remote.setup_log.snapshot()['lines']),1)
            self.assertIn('installing', (remote.journal.path/'setup-stderr.log').read_text())

    def test_silent_process_timeout_is_bounded_and_failure_logs_survive(self):
        with tempfile.TemporaryDirectory() as d:
            remote=Remote('user@robot',Journal(Path(d)/'session'))
            with self.assertRaises(subprocess.TimeoutExpired):
                remote.setup_command([sys.executable,'-c','import time;time.sleep(30)'],.1)
            with self.assertRaisesRegex(CalibrationError,'退出码 7'):
                remote.setup_command([sys.executable,'-c',"import sys;print('Permission denied',file=sys.stderr);sys.exit(7)"],3)
            self.assertIn('Permission denied','\n'.join(remote.setup_log.snapshot()['lines']))

    def test_log_bounded_and_new_run_clears_previous_error(self):
        log=SetupLog();log.begin()
        for i in range(500):log.append(str(i)+'x'*5000)
        data=log.snapshot();self.assertEqual(len(data['lines']),300)
        self.assertLessEqual(len(data['lines'][0]),2010)
        log.finish();self.assertFalse(log.snapshot()['running'])
        log.begin();self.assertEqual(log.snapshot(),{'run':2,'running':True,'lines':[]})

    def test_http_log_is_readable_while_preparation_holds_app_lock(self):
        with tempfile.TemporaryDirectory() as d:
            app=App('user@robot',Path(d));app.setup_log.begin();app.setup_log.append('SSH connecting')
            httpd=ThreadingHTTPServer(('127.0.0.1',0),handler(app))
            thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
            try:
                with app.lock:
                    connection=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=2)
                    connection.request('GET','/api/setup-log');response=connection.getresponse()
                    self.assertEqual(response.status,200);self.assertTrue(json.loads(response.read())['running']);connection.close()
                    connection=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=2)
                    connection.request('GET','/api/setup-log',headers={'Host':'attacker.test'})
                    response=connection.getresponse();self.assertEqual(response.status,403);response.read();connection.close()
            finally:httpd.shutdown();httpd.server_close();thread.join(2)

    def test_prepare_connection_failure_is_visible_and_ends_log(self):
        with tempfile.TemporaryDirectory() as d:
            remote=Remote('user@robot',Journal(Path(d)/'session'))
            remote.stage=Mock(side_effect=OSError('SSH unavailable'))
            with self.assertRaisesRegex(CalibrationError,'尚未停止控制服务'):remote.prepare()
            log=remote.setup_log.snapshot();self.assertFalse(log['running'])
            self.assertIn('SSH unavailable','\n'.join(log['lines']));self.assertIsNone(remote.process)

    def test_removed_requests_never_reach_actuators(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(None,Path(d));app.backend=Mock()
            maintenance=agent.Maintenance('/dev/ttyS2');maintenance.session=Mock()
            for request in ({'action':'probe','confirmed':True},
                            {'action':'zero','confirmed':True,'ids':[14,24]},
                            {'action':'zero','confirmed':True,'detached_output':True}):
                request['motion_token']=app.motion_token
                with self.assertRaisesRegex(CalibrationError,'已移除'):app.request(request)
                with self.assertRaisesRegex(CalibrationError,'已移除'):maintenance.handle(request)
            app.backend.call.assert_not_called();maintenance.session.start_zero.assert_not_called()
