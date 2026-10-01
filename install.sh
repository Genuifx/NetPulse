#!/bin/bash
# NetPulse 一键安装运行脚本
# 用法: bash <(curl -sL https://raw.githubusercontent.com/Genuifx/NetPulse/main/install.sh) [--share]
set -e

REPO="Genuifx/NetPulse"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "正在下载 NetPulse ..."
curl -sL "https://raw.githubusercontent.com/$REPO/main/netpulse.py" -o "$WORKDIR/netpulse.py"

if ! python3 -c "import requests" 2>/dev/null; then
  echo "正在安装依赖 requests ..."
  (pip install --quiet requests || pip3 install --quiet requests) || {
    echo "依赖安装失败，请手动运行：pip install requests"
    exit 1
  }
fi

python3 "$WORKDIR/netpulse.py" "$@"
