#!/bin/sh
# Optional application binary; reuse the existing crate without source changes.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
FIRMWARE=$(CDPATH= cd -- "$ROOT/.build/upstream/microduck-hd1910/firmware" && pwd)
: "${CARGO_TARGET_DIR:=$ROOT/.build/tofd-target}"
export CARGO_TARGET_DIR
cd "$FIRMWARE"
cargo +1.99.0 zigbuild --locked --release --target aarch64-unknown-linux-gnu.2.31 -p tof
printf 'tofd: %s/aarch64-unknown-linux-gnu/release/tofd\n' "$CARGO_TARGET_DIR"
python3 - "$CARGO_TARGET_DIR/aarch64-unknown-linux-gnu/release/tofd" <<'PY'
from pathlib import Path
import hashlib,json,re,struct,subprocess,sys
p=Path(sys.argv[1]);data=p.read_bytes()
assert data[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<H',data,18)[0]==183
versions=sorted({m.decode() for m in re.findall(rb'GLIBC_(\d+\.\d+)',data)},key=lambda v:tuple(map(int,v.split('.'))))
assert versions and tuple(map(int,versions[-1].split('.')))<=(2,31)
record={'program':'tofd','architecture':'AArch64 Linux','sha256':hashlib.sha256(data).hexdigest(),
        'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'firmware_tracked_changes':subprocess.run(['git','diff','--quiet','HEAD','--','.']).returncode!=0,
        'cargo_lock_sha256':hashlib.sha256(Path('Cargo.lock').read_bytes()).hexdigest(),
        'rust':'1.99.0','target':'aarch64-unknown-linux-gnu.2.31','glibc_versions':versions,
        'executed_on_hardware':False}
p.with_name('TOFD-BUILD.json').write_text(json.dumps(record,indent=2)+'\n')
PY
