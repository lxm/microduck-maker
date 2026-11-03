#!/usr/bin/env python3
"""Build a flashable A733 image from pinned Armbian, existing applications and BSP."""
import argparse
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import tarfile

import image_fs
import install

ROOT = Path(__file__).resolve().parent
REPO = ROOT/'.build/upstream'
PROFILE = json.loads((ROOT/'profile.json').read_text())
KERNEL = PROFILE['kernel']


def sha(path):
    return image_fs.digest(path)


def require_hash(path, expected):
    if not path.is_file() or path.is_symlink() or sha(path) != expected:
        raise ValueError('Input missing or hash mismatch: '+str(path))


def run(*args):
    p = subprocess.run(list(map(str, args)), capture_output=True, text=True)
    if p.returncode: raise RuntimeError(p.stdout+p.stderr)
    return p.stdout+p.stderr


def arm64(path):
    with path.open('rb') as f: header = f.read(64)
    if header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != 183:
        raise ValueError('Expected ARM64 ELF: '+str(path))


def extract_app(archive, dest):
    # Keep all shipped files unchanged; never execute a board-specific installer.
    with tarfile.open(archive, 'r:gz') as tf:
        for m in tf:
            parts = PurePosixPath(m.name).parts
            if not parts or parts[0] != 'Microduck-Robotd-FT6-R17' or '..' in parts:
                raise ValueError('Unexpected application archive path')
            if len(parts) == 1: continue
            p = dest/Path(*parts[1:])
            if m.isdir(): p.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                p.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(m) as src, p.open('xb') as dst: shutil.copyfileobj(src, dst)
                p.chmod(m.mode & 0o777)
            else: raise ValueError('Unexpected link or special application file')
    for line in (dest/'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        if Path(name).is_absolute() or '..' in Path(name).parts: raise ValueError('Invalid checksum path')
        require_hash(dest/name, digest)
    for name in ('robotd', 'padd', 'mediad'): arm64(dest/name)


def build(a):
    if a.output.exists(): raise ValueError('Output must be new')
    for tool in ('debugfs', 'e2fsck', 'resize2fs', 'dtc', 'fdtoverlay', 'xz'):
        if not shutil.which(tool): raise ValueError('Missing tool: '+tool)
    if a.reindex_modules and not shutil.which('depmod'): raise ValueError('Missing tool: depmod')
    require_hash(a.base_image, PROFILE['base_image']['sha256'])
    require_hash(a.boot0, PROFILE['boot0']['sha256'])
    bundle = a.application or REPO/PROFILE['application']['repository_path']
    expected = a.application_sha256 if a.application else PROFILE['application']['sha256']
    if not expected: raise ValueError('--application requires --application-sha256')
    require_hash(bundle, expected)
    arm64(a.tofd)
    record = json.loads((a.modules/'MODULES.json').read_text())
    if record['kernel'] != KERNEL or set(record['modules']) != set(install.MODULES):
        raise ValueError('Wrong module set/kernel')
    sources = json.loads((ROOT/'audio/sources.json').read_text())['files']
    for name, hashes in sources.items():
        if record['sources'].get(Path(name).name) != hashes['patched_sha256']:
            raise ValueError('Audio source provenance mismatch: '+name)
    if record['sources'].get('Makefile') != sha(ROOT/'audio/Makefile'):
        raise ValueError('Audio Makefile mismatch')
    for name, digest in record['modules'].items():
        require_hash(a.modules/'modules'/name, digest); arm64(a.modules/'modules'/name)
    if not a.reindex_modules:
        if not {'modules.dep', 'modules.dep.bin', 'modules.alias', 'modules.alias.bin'} <= set(record['indexes']):
            raise ValueError('Missing module indexes')
        for name, digest in record['indexes'].items():
            if Path(name).name != name or not name.startswith('modules.'): raise ValueError('Invalid index name')
            require_hash(a.modules/'indexes'/name, digest)
    a.output.mkdir(parents=True, mode=0o700)
    work = a.output/'work'; work.mkdir()
    payload = a.output/'payload'; payload.mkdir()

    def copy(src, target):
        dest = payload/target; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    def write(target, text):
        dest = payload/target; dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text); dest.chmod(0o644)

    fs = work/'base.ext4'
    with a.base_image.open('rb') as src, fs.open('xb') as dst:
        src.seek(33554432); shutil.copyfileobj(src, dst, 4*1024*1024)
    dtb = work/'base.dtb'; env = work/'armbianEnv.txt'
    run('debugfs', '-R', f'dump /boot/dtb-{KERNEL}/allwinner/sun60i-a733-orangepi-zero3w.dtb "{dtb}"', fs)
    run('debugfs', '-R', f'dump /boot/armbianEnv.txt "{env}"', fs)
    require_hash(dtb, install.DTB_SHA256)
    write('boot/armbianEnv.txt', install.boot_env(env.read_text()))
    dtbos = []
    for name in install.OVERLAYS:
        dest = work/(name+'.dtbo')
        run('dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', dest, ROOT/'overlays'/(name+'.dts'))
        copy(dest, 'boot/overlay-user/'+dest.name); dtbos.append(dest)
    run('fdtoverlay', '-i', dtb, '-o', work/'merged.dtb', *dtbos)
    module_root = work/'module-root'
    modules = module_root/'lib/modules'; modules.mkdir(parents=True)
    run('debugfs', '-R', f'rdump /usr/lib/modules/{KERNEL} "{modules}"', fs)
    files = {str(p.relative_to(modules/KERNEL)): sha(p) for p in sorted((modules/KERNEL).rglob('*'))
             if p.is_file() and not p.is_symlink()}
    base_tree_sha = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    indexes = a.modules/'indexes'
    if a.reindex_modules:
        # Linux preserves case-distinct module filenames that a macOS export can collapse.
        # Index the exact pinned base plus the verified existing .ko files; never compile.
        updates = modules/KERNEL/'updates/microduck-audio'; updates.mkdir(parents=True)
        for name in record['modules']: shutil.copy2(a.modules/'modules'/name, updates/name)
        run('depmod', '-b', module_root, KERNEL)
        indexes = modules/KERNEL
        record['indexes'] = {p.name: sha(p) for p in sorted(indexes.glob('modules.*')) if p.is_file()}
        record['original_prebuilt_base_tree_sha256'] = record['base_tree_sha256']
        record['base_tree_sha256'] = base_tree_sha
        record['indexes_origin'] = 'regenerated by offline depmod against the pinned base image'
        record['note'] = 'Audio modules reused unchanged; module indexes regenerated for this image.'
    elif base_tree_sha != record['base_tree_sha256']:
        raise ValueError('Module index baseline mismatch; on Linux use --reindex-modules')
    for name in record['modules']: copy(a.modules/'modules'/name, f'usr/lib/modules/{KERNEL}/updates/microduck-audio/{name}')
    for name in record['indexes']: copy(indexes/name, f'usr/lib/modules/{KERNEL}/{name}')
    (a.output/'MODULES.json').write_text(json.dumps(record, indent=2)+'\n')
    copy(ROOT/'config/microduck-audio.conf', 'etc/modules-load.d/microduck-audio.conf')
    copy(ROOT/'config/70-microduck-hat-i2c.rules', 'etc/udev/rules.d/70-microduck-hat-i2c.rules')
    for tty in install.GETTYS:
        p = payload/f'etc/systemd/system/serial-getty@{tty}.service'
        p.parent.mkdir(parents=True, exist_ok=True); p.symlink_to('/dev/null')
    copy(a.boot0, 'usr/lib/linux-u-boot-vendor-orangepizero3w/boot0_sdcard.fex')
    app = payload/'opt/robot/feetech-ft5-r5'; extract_app(bundle, app)
    # Original templates, not a calibration from another robot. No runtime code changes.
    copy(app/'robotd-profile.toml', 'etc/robot/feetech-ft5/robotd.toml')
    copy(app/'hd1910-calibration.template.toml', 'etc/robot/feetech-ft5/hd1910-calibration.toml')
    copy(a.tofd, 'opt/robot/tof-stage1/tofd')
    (payload/'opt/robot/tof-stage1/tofd').chmod(0o755)
    write('etc/sysusers.d/microduck.conf', 'g robot -\n')
    firmware = REPO/'microduck-hd1910/firmware'
    for daemon, crate in (('robotd', 'robotd'), ('tofd', 'tof')):
        unit = daemon + '.service'
        copy(firmware/crate/'systemd'/unit, f'usr/lib/systemd/system/{unit}')
        copy(ROOT/'config'/(daemon + '.conf'), f'etc/systemd/system/{unit}.d/10-orangepi.conf')
    copy(REPO/'microduck-hd1910/firmware/deploy/audio/aic3104-init.sh', 'usr/lib/microduck/aic3104-init.sh')
    copy(ROOT/'config/microduck-audio-init.service', 'etc/systemd/system/microduck-audio-init.service')
    wants = payload/'etc/systemd/system/multi-user.target.wants'; wants.mkdir(parents=True)
    (wants/'microduck-audio-init.service').symlink_to('/etc/systemd/system/microduck-audio-init.service')
    # Reuse Armbian's first-run account setup/SSH key regeneration; no new provisioning daemon.
    write('etc/default/armbian-firstrun', 'OPENSSHD_REGENERATE_HOST_KEYS=true\n')
    if a.wifi_netplan:
        copy(a.wifi_netplan, 'etc/netplan/30-microduck-wifi.yaml')
        (payload/'etc/netplan/30-microduck-wifi.yaml').chmod(0o600)
    manifest = {'board': PROFILE['board'], 'kernel': KERNEL, 'application_archive_sha256': sha(bundle),
                'upstream_commit': (ROOT/'upstream.rev').read_text().strip(),
                'application_modified': False, 'modules': record['modules'], 'tofd_sha256': sha(a.tofd),
                'audio_compiled_by_image_builder': False, 'robotd_autostart': False,
                'hardware_tested_this_image': False, 'operator_wifi_included': bool(a.wifi_netplan),
                'module_indexes_regenerated': a.reindex_modules, 'base_module_tree_sha256': base_tree_sha}
    write('etc/microduck/build.json', json.dumps(manifest, indent=2)+'\n')
    (a.output/'BUILD.json').write_text(json.dumps(manifest, indent=2)+'\n')
    fs.unlink()
    image_fs.build(a.base_image, payload, a.boot0, a.output)
    img = a.output/'release/Microduck_OrangePiZero3W_Experimental.img'
    print('Compressing and verifying image...', flush=True)
    run('xz', '-T2', '-6', '-k', img)
    xz = img.with_suffix('.img.xz'); digest = hashlib.sha256(); size = 0
    with lzma.open(xz, 'rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''): digest.update(block); size += len(block)
    if size != img.stat().st_size or digest.hexdigest() != sha(img): raise ValueError('Compression roundtrip failed')
    (img.parent/'SHA256SUMS').write_text(''.join(sha(p)+'  '+p.name+'\n' for p in (img, xz)))
    print('Verified flashable image: '+str(xz), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('base-image', 'boot0', 'modules', 'tofd', 'output'): p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--application', type=Path, help='optional rebuilt application archive; otherwise use repository release')
    p.add_argument('--application-sha256')
    p.add_argument('--reindex-modules', action='store_true', help='run offline Linux depmod using existing audio modules; no compilation')
    p.add_argument('--wifi-netplan', type=Path, help='optional private Netplan file; never executed by the builder')
    args = p.parse_args()
    for key, value in vars(args).items():
        if isinstance(value, Path):
            value = value.resolve()
            if any(c in str(value) for c in '\r\n"\\'): p.error('Unsupported path characters')
            setattr(args, key, value)
    build(args)
