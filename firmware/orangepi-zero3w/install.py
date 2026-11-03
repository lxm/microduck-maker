#!/usr/bin/env python3
"""Install only A733 BSP files; no application changes, module compilation or reboot."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
KERNEL = '6.6.98-vendor-sun60iw2'
DTB_SHA256 = '1eb1160f92dd1d566af920002583c67fbbb6eb35d190a62fb8f0bc1236bb74e1'
OVERLAYS = ['microduck-zero3w-kissqy-uart', 'microduck-zero3w-uart48m-v2',
            'microduck-zero3w-hat-i2c', 'microduck-zero3w-audio-modules']
MODULES = ['snd-soc-tlv320aic3x.ko', 'snd-soc-tlv320aic3x-i2c.ko', 'snd-soc-microduck-aic3104.ko']
GETTYS = ['ttyS0', 'ttyS2', 'ttyAS0', 'ttyAS2']


def run(*args):
    return subprocess.check_output(list(map(str, args)), text=True, stderr=subprocess.STDOUT).strip()


def boot_env(text):
    values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line and not line.startswith('#'))
    known = set(OVERLAYS + ['microduck-zero3w-uart48m'])
    if values.get('overlays', '').strip() or set(values.get('user_overlays', '').split()) - known:
        raise ValueError('Additional overlays require a pin/clock conflict review before installation')
    extra = [s for s in values.get('extraargs', '').split()
             if not s.startswith(('console=ttyS', 'console=ttyAS', 'earlyprintk', 'earlycon'))]
    replacements = {'console': 'display', 'earlycon': 'off', 'user_overlays': ' '.join(OVERLAYS),
                    'extraargs': ' '.join(extra)}
    lines = [line for line in text.splitlines() if line.split('=', 1)[0] not in replacements]
    return '\n'.join(lines + [k+'='+v for k, v in replacements.items()])+'\n'


def copy_atomic(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(source.read_bytes()); f.flush(); os.fsync(f.fileno())
            os.fchmod(f.fileno(), source.stat().st_mode & 0o777)
        os.replace(name, target)
    finally:
        if os.path.exists(name): os.unlink(name)


def install(files, masks, backup, env):
    # Back up every managed path, including whether it originally existed.
    targets = list(files) + masks
    existed = {str(p): p.exists() or p.is_symlink() for p in targets}
    for p in targets:
        if existed[str(p)]:
            dst = backup/str(p).lstrip('/'); dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst, follow_symlinks=False)
    (backup/'state.json').write_text(json.dumps(existed, indent=2)+'\n')
    try:
        for dst, src in files.items():
            if dst != env: copy_atomic(src, dst)
        for p in masks:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.unlink(missing_ok=True); p.symlink_to('/dev/null')
        run('depmod', '-a', KERNEL)
        copy_atomic(files[env], env)  # Enable overlays only after modules/indexes are ready.
    except BaseException:
        for p in targets:
            p.unlink(missing_ok=True)
            if existed[str(p)]:
                shutil.copy2(backup/str(p).lstrip('/'), p, follow_symlinks=False)
        subprocess.run(['depmod', '-a', KERNEL], check=False)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('modules', type=Path, help='directory containing the three prebuilt .ko files')
    args = parser.parse_args()
    if os.geteuid() != 0 or platform.release() != KERNEL or platform.machine() != 'aarch64':
        raise SystemExit('Run as root on the pinned ARM64 vendor kernel')
    if b'xunlong,orangepi-zero3w' not in Path('/sys/firmware/devicetree/base/compatible').read_bytes().split(b'\0'):
        raise SystemExit('Not an Orange Pi Zero 3W')
    dtb = Path('/boot/dtb/allwinner/sun60i-a733-orangepi-zero3w.dtb')
    if hashlib.sha256(dtb.read_bytes()).hexdigest() != DTB_SHA256:
        raise SystemExit('Base DTB differs from the tested Armbian baseline')
    for tool in ('dtc', 'fdtoverlay', 'modinfo', 'depmod'):
        if not shutil.which(tool): raise SystemExit('Missing tool: '+tool)
    for name in MODULES:
        if run('modinfo', '-F', 'vermagic', args.modules/name) != KERNEL+' SMP preempt mod_unload aarch64':
            raise SystemExit('Module ABI mismatch: '+name)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp); files = {}; dtbos = []
        for name in OVERLAYS:
            dest = stage/(name+'.dtbo')
            run('dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', dest, ROOT/'overlays'/(name+'.dts'))
            dest.chmod(0o644); dtbos.append(dest)
            files[Path('/boot/overlay-user')/dest.name] = dest
        run('fdtoverlay', '-i', dtb, '-o', stage/'merged.dtb', *dtbos)
        env = Path('/boot/armbianEnv.txt'); staged_env = stage/env.name
        staged_env.write_text(boot_env(env.read_text())); staged_env.chmod(env.stat().st_mode & 0o777)
        for name in MODULES:
            files[Path('/lib/modules')/KERNEL/'updates/microduck-audio'/name] = args.modules/name
        files[Path('/etc/modules-load.d/microduck-audio.conf')] = ROOT/'config/microduck-audio.conf'
        files[Path('/etc/udev/rules.d/70-microduck-hat-i2c.rules')] = ROOT/'config/70-microduck-hat-i2c.rules'
        files[env] = staged_env
        backup = Path(tempfile.mkdtemp(prefix='microduck-zero3w-', dir='/var/backups'))
        print('Backup: '+str(backup), flush=True)
        install(files, [Path('/etc/systemd/system')/('serial-getty@'+t+'.service') for t in GETTYS], backup, env)
    print('BSP installed. No live overlay/module load or reboot requested; arrange a supported cold boot.')


if __name__ == '__main__': main()
