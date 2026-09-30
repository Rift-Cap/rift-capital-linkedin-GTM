import logging
import sys
import time
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit, urlunsplit

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


def parse_account_url(url: str | None) -> tuple[str, str] | None:
    """LinkedIn account URL -> (type, slug) with type 'person' (/in/<slug>) or
    'company' (/company/<slug>); None if the URL is not one of those."""
    raw = (url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    p = urlsplit(raw)
    host = p.netloc.lower().split(":")[0]
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return None
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 2 or parts[0] not in ("in", "company"):
        return None
    slug = unquote(parts[1]).strip()
    if not slug:
        return None
    return ("person" if parts[0] == "in" else "company"), slug


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
