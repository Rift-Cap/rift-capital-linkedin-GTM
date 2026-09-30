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


def env_bool(name: str, default: bool) -> bool:
    return env(name, str(default)).strip().lower() in ("1", "true", "yes", "y")


# ---- Discovery -------------------------------------------------------------
KEYWORDS = [k.strip() for k in env("KEYWORDS", "secondary market").split(",") if k.strip()]
DATE_POSTED = env("DATE_POSTED", "past_week")  # past_day | past_week | past_month
SEARCH_MAX_RESULTS = int(env("SEARCH_MAX_RESULTS", "30"))

# ---- Engagement ------------------------------------------------------------
PER_CATEGORY_LIMIT = int(env("PER_CATEGORY_LIMIT", "40"))
RESCRAPE_WINDOW_DAYS = int(env("RESCRAPE_WINDOW_DAYS", "7"))
MIN_RESCRAPE_INTERVAL_HOURS = int(env("MIN_RESCRAPE_INTERVAL_HOURS", "24"))
MAX_POSTS_PER_RUN = int(env("MAX_POSTS_PER_RUN", "15"))
DELAY_MIN_S = float(env("DELAY_MIN_S", "5"))
DELAY_MAX_S = float(env("DELAY_MAX_S", "15"))
INCLUDE_REPLIES = env_bool("INCLUDE_REPLIES", True)

# ---- Qualification / outreach ---------------------------------------------
QUALIFY_MIN_SCORE = int(env("QUALIFY_MIN_SCORE", "70"))
# If true, qualified engagers wait for you to set Status=Approved in Notion
# before anything is pushed to lemlist.
REQUIRE_APPROVAL = env_bool("REQUIRE_APPROVAL", True)
CLAUDE_MODEL = env("CLAUDE_MODEL", "claude-sonnet-4-5")

# ---- Orchestrator ----------------------------------------------------------
SUBPROCESS_TIMEOUT_S = 900
