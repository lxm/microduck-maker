#!/bin/sh
set -eu
calibrator_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$calibrator_dir/server.py" "$@"
