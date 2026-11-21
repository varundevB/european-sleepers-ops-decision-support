"""Where things live. The challenge data is never copied into this repo."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "reference"
BUILD = ROOT / "build"
DATA_DIR = Path(os.environ.get("RELAY_DATA", "/tmp/nd-platform1/Platform1-main/data"))
FLEET_XLSX = DATA_DIR / "Fleetmanagement-2026sandbox.xlsx"
WEEKINFO_XLSX = DATA_DIR / "Weekinfo details per train.xlsx"
