#!/usr/bin/env python3
"""Board preparation and passive port inventory. Never opens a UART or stops services."""
import argparse
import glob
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import time

ENV_ROOT = Path('/opt/robot/microduck-calibrator')
VENV = ENV_ROOT / 'venv'
PATTERNS = ('/dev/serial[0-9]*', '/dev/serial/by-id/*', '/dev/serial/by-path/*',
            '/dev/ttyS[0-9]*', '/dev/ttyAMA[0-9]*', '/dev/ttyUSB[0-9]*',
            '/dev/ttyACM[0-9]*', '/dev/ttyTHS[0-9]*', '/dev/ttySC[0-9]*',
            '/dev/ttymxc[0-9]*', '/dev/ttyO[0-9]*')
SERIAL_CHECK = ('import inspect, serial; from serial.tools import list_ports; '
                'assert callable(serial.Serial); assert callable(list_ports.comports); '
                'assert "exclusive" in inspect.signature(serial.Serial).parameters')


def port_argument(value):
    if value == 'auto':
        return value
    if not isinstance(value, str) or not re.fullmatch(
            r'/dev/(?:serial[0-9]+|tty(?:S|AMA|USB|ACM|THS|SC|mxc|O)[0-9]+'
            r'|serial/by-(?:id|path)/[A-Za-z0-9_.:+-]+)', value):
        raise ValueError('串口须为 auto 或 /dev 下的 UART、USB/ACM、serial/by-id/by-path 设备路径')
    return value


def runtime_port():
    """Read the configured port even when torque is on; this is not an entry gate."""
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(2)
            sock.connect('/run/robotd.sock')
            sock.sendall(b'{"jsonrpc":"2.0","id":1,"method":"robot.busStatus","params":{}}\n')
            with sock.makefile('rb') as stream:
                reply = json.loads(stream.readline(2 * 1024 * 1024))
            if isinstance(reply, dict) and reply.get('id') == 1 and isinstance(reply.get('result'), dict):
                value = reply['result'].get('port')
                if value:
                    return port_argument(value)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    legacy = Path('/run/lxm-kissqy-hardware.json')
    try:
        if 0 <= time.time() - legacy.stat().st_mtime < 2:
            value = json.loads(legacy.read_text()).get('port')
            if value:
                return port_argument(value)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def inventory():
    candidates = {p for pattern in PATTERNS for p in glob.glob(pattern)}
    descriptions = {}
    try:
        from serial.tools.list_ports import comports
        for item in comports(include_links=True):
            candidates.add(item.device)
            descriptions[item.device] = str(item.description or '')
    except (ImportError, OSError):
        pass
    hint = runtime_port()
    if hint:
        candidates.add(hint)
    groups = {}
    for value in sorted(candidates):
        try:
            port_argument(value)
            path = Path(value)
            resolved = str(path.resolve(strict=True))
            port_argument(resolved)
            if not stat.S_ISCHR(path.stat().st_mode):
                continue
        except (OSError, ValueError, RuntimeError):
            continue
        row = groups.setdefault(resolved, dict(device=resolved, aliases=[], description=''))
        row['aliases'].append(value)
        if descriptions.get(value):
            row['description'] = descriptions[value]
    ports = []
    for resolved, row in sorted(groups.items()):
        aliases = sorted(set(row['aliases'] + [resolved]))
        row['aliases'] = aliases
        row['device'] = next((p for p in aliases if p.startswith('/dev/serial/by-id/')), resolved)
        row['runtime'] = hint in aliases
        ports.append(row)
    return dict(ports=ports, runtime_port=hint, uart_opened=False, services_changed=False)


def select_port(requested, found):
    port_argument(requested)
    ports = found.get('ports', [])
    if requested != 'auto':
        for item in ports:
            if requested == item['device'] or requested in item.get('aliases', []):
                hint = found.get('runtime_port')
                if hint and hint not in item.get('aliases', []) and hint != item['device']:
                    raise ValueError('所选串口与 robotd 使用的串口不符，请核对配置后重试')
                return requested
        raise ValueError('所选串口已不存在或不是发现的串口，请重新检查环境与串口')
    if found.get('runtime_port'):
        for item in ports:
            if item.get('runtime'):
                return item['device']
        raise ValueError('robotd 配置的串口未发现，请核对设备连接和运行时配置')
    if len(ports) == 1:
        return ports[0]['device']
    if not ports:
        raise ValueError('没有发现串口，请检查 USB 接线、UART 配置和设备节点')
    raise ValueError('发现多个串口，无法确定舵机总线，请在下拉列表中选择')


def serial_available(python):
    try:
        result = subprocess.run([str(python), '-c', SERIAL_CHECK], capture_output=True, timeout=12)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def install_command(args):
    # Installation output belongs on stderr: stdout is the JSON protocol.
    print('准备依赖：' + ' '.join(map(str, args)), file=sys.stderr, flush=True)
    subprocess.run(list(map(str, args)), stdin=subprocess.DEVNULL, stdout=sys.stderr,
                   stderr=sys.stderr, check=True, timeout=100,
                   env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive', 'NEEDRESTART_MODE': 'l'})


def apt_install(packages):
    if not shutil.which('apt-get'):
        raise RuntimeError('自动补齐系统依赖需要 apt-get；请预装 python3-venv 和 psmisc 后重试')
    install_command(['apt-get', '-o', 'DPkg::Lock::Timeout=30', 'update'])
    install_command(['apt-get', '-o', 'DPkg::Lock::Timeout=30', 'install', '-y',
                     '--no-install-recommends', *packages])


def prepare(allow_install=True):
    if sys.version_info < (3, 10):
        raise RuntimeError('板端需要 Python 3.10+，工具不会自动升级系统 Python')
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('板端环境准备需要 Linux 与 sudo -n 权限')
    if not shutil.which('systemctl'):
        raise RuntimeError('板端缺少 systemctl，请核对运行环境')
    installed = []
    if not shutil.which('fuser'):
        if not allow_install:
            raise RuntimeError('缺少 fuser；安装 psmisc，或启用自动安装后重试')
        apt_install(['psmisc'])
        installed.append('psmisc')
    python = sys.executable
    if serial_available(python):
        source = 'system'
    elif serial_available(VENV / 'bin/python'):
        python = str(VENV / 'bin/python')
        source = 'isolated-venv'
    else:
        if not allow_install:
            raise RuntimeError('缺少 pyserial；启用自动安装或在板端预先安装 pyserial 后重试')
        ENV_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            install_command([sys.executable, '-m', 'venv', str(VENV)])
        except subprocess.CalledProcessError:
            apt_install(['python3-venv'])
            install_command([sys.executable, '-m', 'venv', str(VENV)])
        python = str(VENV / 'bin/python')
        install_command([python, '-m', 'pip', '--isolated', '--disable-pip-version-check',
                         '--no-input', 'install', '--index-url', 'https://pypi.org/simple',
                         'pyserial==3.5'])
        if not serial_available(python):
            raise RuntimeError('pyserial 安装后导入核验失败')
        source = 'isolated-venv'
        installed.append('pyserial==3.5')
    if not shutil.which('fuser'):
        raise RuntimeError('fuser 安装后核验失败')
    # Use the selected interpreter for discovery, so its pySerial USB metadata is available.
    result = subprocess.run([python, str(Path(__file__).resolve()), '--discover'],
                            capture_output=True, text=True, check=True, timeout=15)
    found = json.loads(result.stdout)
    return {**found, 'python':python, 'dependency_source':source, 'installed':installed, 'ready':True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discover', action='store_true')
    parser.add_argument('--no-install-deps', action='store_true')
    args = parser.parse_args()
    try:
        if args.discover:
            result = inventory()
        else:
            import fcntl
            ENV_ROOT.mkdir(parents=True, exist_ok=True)
            with (ENV_ROOT / '.setup.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = prepare(not args.no_install_deps)
        print(json.dumps({**result, 'ok':True}, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(json.dumps(dict(ok=False, ready=False, error=str(exc),
                              uart_opened=False, services_changed=False), ensure_ascii=False), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
