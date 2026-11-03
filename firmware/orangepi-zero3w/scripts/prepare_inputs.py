#!/usr/bin/env python3
"""Extract pinned kernel headers/modules from a regular IMG without mounting it."""
import argparse
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_image import PROFILE, require_hash, run

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--base-image',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
require_hash(a.base_image,PROFILE['base_image']['sha256'])
out=a.output.resolve()
if out.exists():raise SystemExit('Output already exists')
if any(c in str(out) for c in '\r\n"\\'):raise SystemExit('Unsupported output path')
out.mkdir(parents=True)
fs=out/'base.ext4'
with a.base_image.open('rb') as source,fs.open('xb') as dest:
    source.seek(33554432);shutil.copyfileobj(source,dest,4*1024*1024)
k=PROFILE['kernel']
for dest,src in [('headers','/usr/src/linux-headers-'+k),('modules','/usr/lib/modules/'+k)]:
    (out/dest).mkdir()
    log=run('debugfs','-R',f'rdump {src} "{out/dest}"',fs)
    (out/(dest+'-extract.log')).write_text(log)
for name,digest in PROFILE['headers_sha256'].items():require_hash(out/'headers'/('linux-headers-'+k)/name,digest)
if not (out/'modules'/k/'modules.dep').is_file():raise SystemExit('Module extraction failed')
fs.unlink()
print(json.dumps({'headers':str(out/'headers'/('linux-headers-'+k)),'base_modules':str(out/'modules'/k)},indent=2))
