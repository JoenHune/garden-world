#!/usr/bin/env bash
# install.sh — Install garden-world and its browser dependency
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo "Installing garden-world from $PROJECT_ROOT ..."
pip install -e "$PROJECT_ROOT"

echo "Installing Playwright Chromium ..."
python3 -m playwright install chromium

echo "Done. Run 'garden-world login' to set up authentication."
