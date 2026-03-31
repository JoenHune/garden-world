"""E2E test: search for March 27 codes using the full pipeline."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from garden_world.main import run

# Clean state
state_path = Path(".garden_world/state.json")
if state_path.exists():
    state_path.unlink()

fake = datetime(2026, 3, 27, 22, 30, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
print(f"Simulating: {fake}")

with patch("garden_world.main._now", return_value=fake):
    rc = run(now_mode=True, force_refresh=True)

print(f"\nExit code: {rc}")

# Show resulting state
if state_path.exists():
    state = json.loads(state_path.read_text())
    print(f"State: {json.dumps(state, ensure_ascii=False, indent=2)}")
