#!/usr/bin/env python3
"""Prepare fixed upstream sources and apply the tested patch; does not compile."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent


def prepare(kernel, output):
    sources = json.loads((ROOT/'sources.json').read_text())['files']
    if output.exists(): raise ValueError('Output directory must be new')
    for name, hashes in sources.items():
        if hashlib.sha256((kernel/name).read_bytes()).hexdigest() != hashes['sha256']:
            raise ValueError('Upstream source mismatch: '+name)
    output.mkdir(parents=True)
    for name in sources: shutil.copy2(kernel/name, output/Path(name).name)
    subprocess.run(['patch', '-p1', '-i', str(ROOT/'vendor-to-module.patch')], cwd=output, check=True)
    # Keep the tested standalone namespace without hundreds of rename-only diff lines.
    for path in output.glob('snd_sunxi_mach*'):
        path.write_text(path.read_text().replace('asoc_simple_', 'microduck_simple_'))
    for name, hashes in sources.items():
        if hashlib.sha256((output/Path(name).name).read_bytes()).hexdigest() != hashes['patched_sha256']:
            raise ValueError('Patched source mismatch: '+name)
    shutil.copy2(ROOT/'Makefile', output/'Makefile')
    shutil.copy2(ROOT/'COPYING', output/'COPYING')
    print('Sources prepared; no compiler or module loader was invoked.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('kernel', type=Path); p.add_argument('output', type=Path)
    a = p.parse_args(); prepare(a.kernel.resolve(), a.output.resolve())
