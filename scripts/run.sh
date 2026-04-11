#!/usr/bin/env bash
# run.sh — 启动花园世界兑换码自动抓取 + 推送
#
# 每天 19:00 开始，每 5 分钟抓取一次兑换码并推送新码到微信。
# 23:30 后停止当天轮询，次日 19:00 重新开始。
#
# 用法:
#   ./scripts/run.sh              # 前台运行
#   ./scripts/run.sh &            # 后台运行
#   nohup ./scripts/run.sh >> .garden_world/run.log 2>&1 &   # 后台 + 日志
#
# 停止: Ctrl+C 或 kill $(cat .garden_world/run.pid)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Activate venv
if [[ -f .venv/bin/activate ]]; then
    source .venv/bin/activate
fi

mkdir -p .garden_world

# Write PID file for easy stop
echo $$ > .garden_world/run.pid
trap 'rm -f .garden_world/run.pid; echo "已停止"; exit 0' INT TERM

echo "=== garden-world 自动运行 ==="
echo "PID: $$"
echo "日志: .garden_world/run.log"
echo "停止: kill $$ 或 Ctrl+C"
echo ""

python -m garden_world daemon
