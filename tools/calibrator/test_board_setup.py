import json
import io
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import board_setup as setup
from core import CalibrationError
from server import App, Remote
from storage import Journal


def found(*devices, hint=None):
    return dict(ok=True, ready=True, python='/opt/robot/microduck-calibrator/venv/bin/python',
                dependency_source='isolated-venv', installed=[], runtime_port=hint,
                ports=[dict(device=p, aliases=[p], runtime=p == hint, description='') for p in devices],
                uart_opened=False, services_changed=False)


class PortTests(unittest.TestCase):
    def test_supported_paths_and_no_shell_paths(self):
        for p in ('auto', '/dev/serial0', '/dev/ttyS3', '/dev/ttyUSB12', '/dev/ttyACM0',
                  '/dev/ttyAMA1', '/dev/serial/by-id/usb-FTDI_123-if00-port0'):
            self.assertEqual(setup.port_argument(p), p)
        for p in (None, True, '/dev/null', '/dev/ttyS2;reboot', '/dev/ttyUSB0$(id)',
                  '/dev/serial/by-id/../null', '/tmp/ttyUSB0', '/dev/ttyUSB0\n', '-x'):
            with self.subTest(p=p), self.assertRaises(ValueError):
                setup.port_argument(p)

    def test_runtime_port_wins_without_probing(self):
        self.assertEqual(setup.select_port('auto', found('/dev/ttyS0', '/dev/ttyS2', hint='/dev/ttyS2')), '/dev/ttyS2')

    def test_multiple_unknown_ports_refuse_guess(self):
        with self.assertRaisesRegex(ValueError, '多个串口'):
            setup.select_port('auto', found('/dev/ttyUSB0', '/dev/ttyUSB1'))

    def test_unique_and_empty_inventory(self):
        self.assertEqual(setup.select_port('auto', found('/dev/ttyUSB0')), '/dev/ttyUSB0')
        with self.assertRaisesRegex(ValueError, '没有发现'):
            setup.select_port('auto', found())

    def test_unplugged_explicit_port_refused(self):
        with self.assertRaisesRegex(ValueError, '已不存在'):
            setup.select_port('/dev/ttyUSB0', found('/dev/ttyUSB1'))

    def test_missing_runtime_port_does_not_fall_back_to_other_device(self):
        with self.assertRaisesRegex(ValueError, 'robotd 配置'):
            setup.select_port('auto', found('/dev/ttyUSB0', hint='/dev/ttyS2'))

    def test_explicit_wrong_bus_refused(self):
        with self.assertRaisesRegex(ValueError, '不符'):
            setup.select_port('/dev/ttyUSB0', found('/dev/ttyS2', '/dev/ttyUSB0', hint='/dev/ttyS2'))

    def test_aliases_deduplicated_and_character_devices_only(self):
        alias='/dev/serial/by-id/usb-FTDI_123'
        paths={alias:'/dev/ttyUSB0', '/dev/serial0':'/dev/ttyUSB0',
               '/dev/ttyUSB0':'/dev/ttyUSB0', '/dev/ttyS0':'/dev/ttyS0'}
        serial=Mock();ports=Mock(comports=Mock(return_value=[]))
        with patch('board_setup.glob.glob', side_effect=lambda pattern:list(paths)), \
             patch('board_setup.runtime_port', return_value='/dev/serial0'), \
             patch.dict(sys.modules, {'serial':serial, 'serial.tools':Mock(), 'serial.tools.list_ports':ports}), \
             patch.object(Path, 'resolve', lambda p, strict=False:Path(paths[str(p)])), \
             patch.object(Path, 'stat', lambda p:Mock(st_mode=stat.S_IFREG if str(p)=='/dev/ttyS0' else stat.S_IFCHR)):
            result=setup.inventory()
        self.assertEqual(len(result['ports']),1)
        row=result['ports'][0]
        self.assertEqual(row['device'],alias)
        self.assertTrue(row['runtime'])
        self.assertEqual(len(row['aliases']),3)
        self.assertEqual(setup.select_port('auto',result),alias)
        serial.Serial.assert_not_called()


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.root=patch('board_setup.os.geteuid',return_value=0);self.root.start();self.addCleanup(self.root.stop)
        self.platform=patch('board_setup.sys.platform','linux');self.platform.start();self.addCleanup(self.platform.stop)
        self.which=patch('board_setup.shutil.which',side_effect=lambda name:'/usr/bin/'+name);self.which.start();self.addCleanup(self.which.stop)
        self.discovery=patch('board_setup.subprocess.run',return_value=Mock(stdout=json.dumps(found('/dev/ttyS2')),returncode=0));self.run=self.discovery.start();self.addCleanup(self.discovery.stop)

    def test_existing_system_dependency_reused_without_install(self):
        with patch('board_setup.serial_available',return_value=True),patch('board_setup.install_command') as install:
            result=setup.prepare()
        self.assertEqual(result['python'],sys.executable)
        self.assertEqual(result['installed'],[])
        install.assert_not_called()
        self.assertIn('--discover',self.run.call_args.args[0])

    def test_existing_private_environment_reused_without_pip(self):
        with patch('board_setup.serial_available',side_effect=[False,True]),patch('board_setup.install_command') as install:
            result=setup.prepare()
        self.assertEqual(result['python'],str(setup.VENV/'bin/python'))
        install.assert_not_called()

    def test_disabled_install_is_read_only_failure(self):
        with patch('board_setup.serial_available',return_value=False),patch('board_setup.install_command') as install:
            with self.assertRaisesRegex(RuntimeError,'缺少 pyserial'):
                setup.prepare(False)
        install.assert_not_called()

    def test_pinned_pyserial_installed_only_into_private_venv(self):
        with tempfile.TemporaryDirectory() as d,patch('board_setup.ENV_ROOT',Path(d)), \
             patch('board_setup.VENV',Path(d)/'venv'), \
             patch('board_setup.serial_available',side_effect=[False,False,True]), \
             patch('board_setup.install_command') as install:
            result=setup.prepare()
            pip=install.call_args_list[-1].args[0]
            self.assertEqual(pip[0],str(Path(d)/'venv/bin/python'))
        self.assertIn('pyserial==3.5',pip)
        self.assertIn('--isolated',pip)
        self.assertNotIn('--break-system-packages',pip)
        self.assertEqual(result['installed'],['pyserial==3.5'])

    def test_missing_venv_module_installed_then_retried(self):
        with tempfile.TemporaryDirectory() as d,patch('board_setup.ENV_ROOT',Path(d)), \
             patch('board_setup.VENV',Path(d)/'venv'), \
             patch('board_setup.serial_available',side_effect=[False,False,True]), \
             patch('board_setup.install_command',side_effect=[subprocess.CalledProcessError(1,'venv'),None,None]) as install, \
             patch('board_setup.apt_install') as apt:
            setup.prepare()
        apt.assert_called_once_with(['python3-venv'])
        self.assertEqual(install.call_count,3)

    def test_failed_install_never_claims_ready(self):
        with tempfile.TemporaryDirectory() as d,patch('board_setup.ENV_ROOT',Path(d)), \
             patch('board_setup.VENV',Path(d)/'venv'), \
             patch('board_setup.serial_available',return_value=False), \
             patch('board_setup.install_command',side_effect=subprocess.TimeoutExpired('pip',100)):
            with self.assertRaises(subprocess.TimeoutExpired):setup.prepare()
        self.run.assert_not_called()

    def test_missing_fuser_disabled_install_does_not_modify_system(self):
        with patch('board_setup.shutil.which',side_effect=lambda n:None if n=='fuser' else '/usr/bin/'+n), \
             patch('board_setup.apt_install') as apt:
            with self.assertRaisesRegex(RuntimeError,'缺少 fuser'):setup.prepare(False)
        apt.assert_not_called()

    def test_no_system_python_upgrade(self):
        with patch('board_setup.sys.version_info',(3,9)),patch('board_setup.install_command') as install:
            with self.assertRaisesRegex(RuntimeError,'Python 3.10'):setup.prepare()
        install.assert_not_called()

    def test_setup_cli_emits_single_ready_json_without_changing_services(self):
        with tempfile.TemporaryDirectory() as d,patch('board_setup.ENV_ROOT',Path(d)), \
             patch('board_setup.sys.argv',['board_setup.py']), \
             patch('board_setup.prepare',return_value=found('/dev/ttyS2')), \
             patch('board_setup.sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(setup.main(),0)
            result=json.loads(output.getvalue())
        self.assertTrue(result['ok']);self.assertTrue(result['ready'])
        self.assertFalse(result['services_changed']);self.assertFalse(result['uart_opened'])


class RemotePreparationTests(unittest.TestCase):
    def remote(self,d,port='auto'):
        return Remote('user@robot.local',Journal(Path(d)/'session'),port,True)

    def test_preparation_does_not_start_maintenance(self):
        with tempfile.TemporaryDirectory() as d:
            remote=self.remote(d);remote.dest='/tmp/duck-calibrator-test'
            with patch('server.subprocess.run',return_value=Mock(returncode=0,stdout=json.dumps(found('/dev/ttyS2')))) as run,patch('server.subprocess.Popen') as popen:
                result=remote.prepare()
            self.assertTrue(result['ready']);popen.assert_not_called()
            command=shlex.split(run.call_args.args[0][-1])
            self.assertEqual(command[:3],['sudo','-n','python3'])
            self.assertTrue(command[3].endswith('/board_setup.py'))

    def test_failed_preparation_never_launches_agent(self):
        with tempfile.TemporaryDirectory() as d:
            remote=self.remote(d);remote.dest='/tmp/duck-calibrator-test'
            with patch('server.subprocess.run',return_value=Mock(returncode=1,stdout=json.dumps({'ok':False,'error':'offline'}))),patch('server.subprocess.Popen') as popen:
                with self.assertRaisesRegex(CalibrationError,'尚未停止控制服务'):remote.start()
            popen.assert_not_called()

    def test_hot_unplug_and_ambiguous_inventory_never_launch_agent(self):
        for port,devices in [('auto',('/dev/ttyUSB0','/dev/ttyUSB1')),('/dev/ttyUSB0',('/dev/ttyUSB1',))]:
            with self.subTest(port=port),tempfile.TemporaryDirectory() as d:
                remote=self.remote(d,port)
                with patch.object(remote,'prepare',return_value=found(*devices)),patch('server.subprocess.Popen') as popen:
                    with self.assertRaises(ValueError):remote.start()
                popen.assert_not_called()

    def test_verified_interpreter_and_selected_usb_reach_agent(self):
        with tempfile.TemporaryDirectory() as d:
            remote=self.remote(d,'/dev/ttyUSB0');remote.dest='/tmp/duck-calibrator-test'
            process=Mock(stdout=[])
            with patch.object(remote,'prepare',return_value=found('/dev/ttyUSB0')), \
                 patch.object(remote,'receive',return_value={'ready':True}), \
                 patch('server.subprocess.Popen',return_value=process) as popen:
                remote.start()
            command=shlex.split(popen.call_args.args[0][-1])
            self.assertEqual(command[2],'/opt/robot/microduck-calibrator/venv/bin/python')
            self.assertIn('/dev/ttyUSB0',command)

    def test_prepare_failure_can_be_retried_and_is_not_active_session(self):
        with tempfile.TemporaryDirectory() as d:
            app=App('user@robot.local',Path(d))
            with patch.object(Remote,'prepare',side_effect=[CalibrationError('offline'),found('/dev/ttyUSB0')]), \
                 patch.object(Remote,'call') as call:
                with self.assertRaises(CalibrationError):app.request({'action':'prepare'})
                self.assertIsNone(app.preparation)
                result=app.request({'action':'prepare'})
            self.assertTrue(result['ready']);self.assertIsNone(app.backend);call.assert_not_called()

    def test_discovery_rejected_during_live_session(self):
        with tempfile.TemporaryDirectory() as d:
            app=App('user@robot.local',Path(d));app.backend=Mock()
            with patch.object(Remote,'prepare') as prepare:
                with self.assertRaisesRegex(CalibrationError,'先结束'):app.request({'action':'prepare'})
            prepare.assert_not_called()

    def test_support_confirmation_precedes_preparation(self):
        with tempfile.TemporaryDirectory() as d:
            app=App('user@robot.local',Path(d))
            with patch.object(Remote,'prepare') as prepare:
                with self.assertRaisesRegex(CalibrationError,'托稳'):app.request({'action':'enter'})
            prepare.assert_not_called();self.assertIsNone(app.backend)


if __name__=='__main__':unittest.main()
