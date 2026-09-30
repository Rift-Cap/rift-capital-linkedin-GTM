import json
import logging
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import requests

from .config import LOG_DIR


def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_DIR / f"{name}.log")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return log


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def canonical_url(url: str | None) -> str:
    """Strip query string, fragment and trailing slash. Path is preserved exactly
    (activity / share / ugcPost permalinks are NOT interchangeable)."""
    if not url:
        return ""
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme or "https", p.netloc.lower(), p.path.rstrip("/"), "", ""))


def split_name(full: str) -> tuple[str, str]:
    parts = (full or "").split()
    return (parts[0], " ".join(parts[1:])) if parts else ("", "")


def extract_json(text: str):
    """Pull the first JSON object/array out of an LLM reply."""
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                return dec.raw_decode(text[i:])[0]
            except json.JSONDecodeError:
                continue
    raise ValueError("No JSON found in model output")


RETRY_STATUS = {429, 500, 502, 503, 504}


def http(method: str, url: str, retries: int = 4, **kw) -> requests.Response:
    """requests with exponential backoff on 429/5xx and network errors."""
    kw.setdefault("timeout", 30)
    for attempt in range(retries + 1):
        try:
            r = requests.request(method, url, **kw)
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(2**attempt)
            continue
        if r.status_code in RETRY_STATUS and attempt < retries:
            ra = r.headers.get("Retry-After", "")
            time.sleep(float(ra) if ra.replace(".", "", 1).isdigit() else 1.5 * 2**attempt)
            continue
        return r
    return r  # pragma: no cover
