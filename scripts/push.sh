#!/usr/bin/env bash
# push.sh — 推送今日兑换码到微信（不启动浏览器，使用已缓存数据）
#
# 用法:
#   ./scripts/push.sh           # 推送已缓存的码
#   ./scripts/push.sh --force   # 强制重发所有码
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Activate venv
if [[ -f .venv/bin/activate ]]; then
    source .venv/bin/activate
fi

python -m garden_world push "$@"
