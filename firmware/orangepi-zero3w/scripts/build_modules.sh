#!/bin/sh
# Native ARM64 Linux build; does not install modules or touch the running kernel.
set -eu
if [ "$#" -ne 4 ]; then echo 'Usage: build_modules.sh KERNEL_SOURCE KERNEL_HEADERS BASE_MODULE_DIR OUTPUT_DIR' >&2; exit 2; fi
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
KSRC=$(realpath "$1")
KDIR=$(realpath "$2")
BASE_MODULES=$(realpath "$3")
OUT=$(realpath -m "$4")
test "$(uname -m)" = aarch64 || { echo 'Use native ARM64 Linux (or an ARM64 build container).' >&2; exit 1; }
test "$(cat "$KDIR/include/config/kernel.release")" = 6.6.98-vendor-sun60iw2
test ! -e "$OUT" || { echo 'Output must be new; refusing to overwrite.' >&2; exit 1; }
python3 - "$ROOT/profile.json" "$KDIR" <<'PY'
import hashlib,json,pathlib,sys
profile=json.loads(pathlib.Path(sys.argv[1]).read_text());root=pathlib.Path(sys.argv[2])
for name,expected in profile['headers_sha256'].items():
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:raise SystemExit('Header/config mismatch: '+name)
PY
mkdir -p "$OUT/modules"
python3 "$ROOT/audio/prepare.py" "$KSRC" "$OUT/src"
make -C "$KDIR" M="$OUT/src" -j2 modules
cp "$OUT"/src/*.ko "$OUT/modules/"
python3 - "$OUT" "$ROOT" "$BASE_MODULES" <<'PY'
import hashlib,json,pathlib,subprocess,sys,shutil
out,root,base=map(pathlib.Path,sys.argv[1:]);records={}
for p in sorted((out/'modules').glob('*.ko')):
    vermagic=subprocess.check_output(['modinfo','-F','vermagic',str(p)],text=True).strip()
    if vermagic!='6.6.98-vendor-sun60iw2 SMP preempt mod_unload aarch64':raise SystemExit('Wrong vermagic')
    if 'AArch64' not in subprocess.check_output(['readelf','-h',str(p)],text=True):raise SystemExit('Wrong architecture')
    records[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
assert len(records)==3
record={'kernel':'6.6.98-vendor-sun60iw2','modules':records,'sources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((out/'src').iterdir()) if p.suffix in ('.c','.h') or p.name == 'Makefile'},'compiler':subprocess.check_output(['cc','--version'],text=True).splitlines()[0],'loaded_on_hardware':False}
base_files={str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(base.rglob('*')) if p.is_file() and not p.is_symlink()}
record['base_tree_sha256']=hashlib.sha256(json.dumps(base_files,sort_keys=True).encode()).hexdigest()
kroot=out/'module-root/lib/modules'/record['kernel']
shutil.copytree(base,kroot,symlinks=True)
updates=kroot/'updates/microduck-audio';updates.mkdir(parents=True,exist_ok=True)
for p in (out/'modules').glob('*.ko'):shutil.copy2(p,updates/p.name)
subprocess.run(['depmod','-b',str(out/'module-root'),record['kernel']],check=True)
(out/'indexes').mkdir()
for p in kroot.glob('modules.*'):
    if p.is_file():shutil.copy2(p,out/'indexes'/p.name)
record['indexes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((out/'indexes').iterdir())}
(out/'MODULES.json').write_text(json.dumps(record,indent=2)+'\n')
PY
