#!/usr/bin/env bash
# bind.sh — 绑定新的微信 ClawBot 账号
#
# 运行后会显示二维码，用微信扫码即可绑定。
# 扫码成功后需要在微信中发一条消息给Bot以激活推送通道。
#
# 用法:
#   ./scripts/bind.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Activate venv
if [[ -f .venv/bin/activate ]]; then
    source .venv/bin/activate
fi

echo "=== 绑定微信 ClawBot ==="
echo "1. 运行后会在终端显示二维码"
echo "2. 用微信扫描二维码"
echo "3. 扫码成功后，在微信中给Bot发一条消息（如\"你好\"）即可完成"
echo ""

python -m garden_world bind
