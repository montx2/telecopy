"""
TELECOPY — Replicador de Canais Telegram
by montx2
"""

__version__ = "3.0.0"
__author__  = "montx2"
__license__ = "MIT"

from pathlib import Path

DATA_DIR     = Path("data")
SESSION_FILE = DATA_DIR / "session"
CONFIG_FILE  = DATA_DIR / "config.json"
DB_FILE      = DATA_DIR / "telecopy.db"
STATS_FILE   = DATA_DIR / "stats.json"
LOGS_DIR     = DATA_DIR / "logs"
VAULT_DIR    = DATA_DIR / "vault"
TEMP_DIR     = DATA_DIR / "tmp"
