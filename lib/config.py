"""Central configuration. Everything secret comes from .env / environment."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

LOG_DIR = ROOT / "05-actions-log"
LOG_DIR.mkdir(exist_ok=True)


def env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise SystemExit(f"Missing required environment variable: {name}")
    return val or ""


# ---- Watchlist fetch -------------------------------------------------------
MAX_POSTS_PER_ACCOUNT = int(env("MAX_POSTS_PER_ACCOUNT", "50"))
REFRESH_WINDOW_DAYS = int(env("REFRESH_WINDOW_DAYS", "7"))
DELAY_MIN_S = float(env("DELAY_MIN_S", "3"))
DELAY_MAX_S = float(env("DELAY_MAX_S", "8"))

# ---- Engagers (who reacted / commented) ------------------------------------
ENGAGER_WINDOW_DAYS = int(env("ENGAGER_WINDOW_DAYS", "7"))
MAX_REACTIONS_PER_POST = int(env("MAX_REACTIONS_PER_POST", "100"))
MAX_COMMENTS_PER_POST = int(env("MAX_COMMENTS_PER_POST", "100"))

# ---- Orchestrator ----------------------------------------------------------
SUBPROCESS_TIMEOUT_S = 900
