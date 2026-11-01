"""RegWatch — Level 1 regulatory change impact agent."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW_DIR = DATA / "raw"
CACHE_DIR = DATA / "cache"
SAMPLES_DIR = DATA / "samples"
DB_PATH = DATA / "regwatch.db"
CONFIG_DIR = ROOT / "config"

for _d in (DATA, RAW_DIR, CACHE_DIR, SAMPLES_DIR):
    _d.mkdir(parents=True, exist_ok=True)
