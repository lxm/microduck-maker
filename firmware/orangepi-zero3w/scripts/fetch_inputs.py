#!/usr/bin/env python3
"""Fetch explicit pinned Armbian inputs; no latest-version lookup or installation."""
import argparse
import lzma
from pathlib import Path
import shutil
import urllib.request
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_image import PROFILE, require_hash


def download(url,path,digest):
    if path.exists():
        require_hash(path,digest);return
    temp=path.with_name(path.name+'.part')
    try:
        with urllib.request.urlopen(url,timeout=60) as source,temp.open('xb') as target:
            shutil.copyfileobj(source,target,1024*1024)
        require_hash(temp,digest);temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    base=PROFILE['base_image'];img=a.output/base['name'];xz=img.with_suffix('.img.xz')
    download(base['url'],xz,base['xz_sha256'])
    if not img.exists():
        temp=img.with_suffix('.part')
        try:
            with lzma.open(xz,'rb') as source,temp.open('xb') as target:shutil.copyfileobj(source,target,4*1024*1024)
            require_hash(temp,base['sha256']);temp.replace(img)
        finally:temp.unlink(missing_ok=True)
    require_hash(img,base['sha256'])
    download(PROFILE['boot0']['url'],a.output/'boot0-1gb.fex',PROFILE['boot0']['sha256'])
    print('Pinned image and boot0 verified. No block device was opened.')


if __name__=='__main__':main()
