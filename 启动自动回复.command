#!/bin/zsh
set -eu
umask 077
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  print "缺少 Python 环境，请按 README 安装 .venv 后重试。"
  read "REPLY?按回车关闭窗口: "
  exit 1
fi
exec "$ROOT/.venv/bin/python" -B -u "$ROOT/setup_local.py"
