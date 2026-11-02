#!/usr/bin/env python3
"""Check the archived wire-solder PCB package without CAD tools or hardware."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = ROOT / 'd0.8.1'


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    m = read(VERSION / 'manifest.json')
    require(m['revision'] == 'D0.8.1', 'Unexpected hardware revision')
    actual = {str(f.relative_to(VERSION)) for f in VERSION.rglob('*')
              if f.is_file() and f.name != 'manifest.json' and '.history' not in f.parts
              and f.suffix not in ('.kicad_prl', '.pyc', '.log') and f.name != '.DS_Store'}
    require(actual == {r['path'] for r in m['files']}, 'Archived file set changed; review and update manifest')
    for r in m['files']:
        f = VERSION / r['path']
        require(sha(f) == r['sha256'] and f.stat().st_size == r['bytes'], f'Hash/size mismatch: {r["path"]}')
    source = read(VERSION / 'source-manifest.json')
    for r in source['source_files']:
        require(sha(VERSION / r['published_path']) == r['published_sha256'],
                f'Source snapshot mismatch: {r["published_path"]}')
    require(read(VERSION / 'validation/package-preservation.json')['status'] == 'PASS', 'Import preservation failed')
    for f in VERSION.rglob('*.kicad_pcb'):
        s = f.read_text()
        require('Dual_TH_SMT' not in s and 'D0.8.2' not in s, f'Unqualified terminal version in {f.name}')
    for f in [*VERSION.rglob('*.kicad_pcb'), *VERSION.rglob('*.kicad_mod')]:
        project = VERSION / 'pcb' if f.suffix == '.kicad_mod' else f.parent
        for name in re.findall(r'\(model "([^"\n]+)"', f.read_text()):
            require(name.startswith('${KIPRJMOD}/'), f'Nonportable model path: {name}')
            path = (project / name.replace('${KIPRJMOD}/', '')).resolve()
            require(path.is_relative_to(VERSION.resolve()) and path.is_file(), f'Missing/outside model: {name}')
    old = VERSION / 'validation/original'
    for n in ['pinfix-verification', 'single-fabrication-qa', 'panel-fabrication-qa', 'panel-unit-parity', 'exported-port-legends']:
        require(read(old / (n + '.json'))['status'] == 'PASS', f'Original check not passing: {n}')
    for n in ['pinfix-drc', 'single-fab-drc', 'panel-drc']:
        d = read(old / (n + '.json'))
        require(not d['violations'] and not d['unconnected_items'], f'Original DRC not clean: {n}')
    d = read(VERSION / 'validation/packaged-drc.json')
    require(not d['violations'] and not d['unconnected_items'] and not d.get('schematic_parity', []),
            'Packaged PCB DRC/parity not clean')
    require(not any(s['violations'] for s in read(VERSION / 'validation/packaged-erc.json')['sheets']),
            'Packaged schematic ERC not clean')
    zips = list((VERSION / 'fabrication').rglob('*.zip'))
    require(len(zips) == 2, 'Expected single and six-up ZIPs only')
    for z in zips:
        with zipfile.ZipFile(z) as archive:
            require(archive.testzip() is None, f'Corrupt ZIP: {z.name}')
            names = archive.namelist()
            expected = {f.name for f in (z.parent / 'gerbers').iterdir() if f.is_file()}
            require(len(names) == 10 and len(set(names)) == 10 and set(names) == expected,
                    f'Unexpected fabrication layer set: {z.name}')
            for n in names:
                require(archive.read(n) == (z.parent / 'gerbers' / n).read_bytes(), f'ZIP differs from Gerber: {n}')
    print('PASS: D0.8.1 source hashes, portable models, pin/DRC/ERC records and two fabrication ZIPs')
    print('Physical harness polarity, assembly and load/temperature acceptance remain pending.')


if __name__ == '__main__':
    main()
