#!/usr/bin/env python3
"""Lightweight orchestrator. Run daily from cron / launchd / GitHub Actions.

Runs fetch_watchlist_posts.py, then fetch_engagers.py, each as a subprocess with a 900s timeout; only the
last lines of output are logged. NEW_POSTS / NEW_ENGAGERS are parsed BEFORE judging the exit code.
Returns 1 on auth failure (exit code 2), timeout or any non-zero exit.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib.config import SUBPROCESS_TIMEOUT_S  # noqa: E402
from lib.util import get_logger  # noqa: E402

log = get_logger("monitor")
W = ROOT / "03-workflows"
AUTH_FAILED = 2
TIMED_OUT = -1


def run(script: str, *args: str) -> tuple[int, str]:
    cmd = [sys.executable, str(W / script), *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_S, cwd=ROOT)
    except subprocess.TimeoutExpired:
        log.error("%s timed out after %ss", script, SUBPROCESS_TIMEOUT_S)
        return TIMED_OUT, ""
    out = (p.stdout or "") + (p.stderr or "")
    log.info("%s exit=%s | %s", script, p.returncode, " / ".join(out.strip().splitlines()[-3:]))
    return p.returncode, out


def parse_int(tag: str, out: str) -> int:
    m = re.search(rf"{tag}=(\d+)", out)
    return int(m.group(1)) if m else 0


def main() -> int:
    failed = False
    for script, tag in (("fetch_watchlist_posts.py", "NEW_POSTS"), ("fetch_engagers.py", "NEW_ENGAGERS")):
        code, out = run(script)
        log.info("%s=%s", tag, parse_int(tag, out))  # read first; exit code is judged after
        if code == AUTH_FAILED:
            log.error("LinkedIn auth failed")
            return 1
        if code != 0:  # includes timeouts (-1)
            log.error("%s failed (exit %s)", script, code)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
