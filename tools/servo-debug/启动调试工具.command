#!/bin/zsh
set -e
trap 'print "启动失败，请保留此终端中的错误信息。"; read "?按回车退出"' ZERR
cd -- "${0:A:h}"
FD_ENV="$HOME/Library/Application Support/MicroDuck FD/venv"
if ! command -v python3 >/dev/null 2>&1; then
  print '需要 Python 3.9 或更新版本，请先从 python.org 安装。'
  read '?按回车退出'
  exit 1
fi
if [[ ! -x "$FD_ENV/bin/python" ]]; then
  python3 -m venv "$FD_ENV"
fi
if ! "$FD_ENV/bin/python" -c 'import serial' >/dev/null 2>&1; then
  print '首次启动：安装串口依赖 pyserial…'
  "$FD_ENV/bin/python" -m pip install 'pyserial==3.5'
fi
exec "$FD_ENV/bin/python" fd_mac.py "$@"
