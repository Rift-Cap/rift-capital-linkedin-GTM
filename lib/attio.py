"""Attio People index used to skip engagers that are already known in the CRM (read-only)."""
import unicodedata
from urllib.parse import unquote, urlsplit

import requests

from .config import env
from .util import http

BASE = "https://api.attio.com/v2"
PAGE = 500


class AttioError(Exception):
    pass


def linkedin_slug(url: str | None) -> str:
    """https://www.linkedin.com/in/<slug>[/...] -> lowercase slug ('' if not a /in/ URL)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parts = [x for x in urlsplit(raw).path.split("/") if x]
    if len(parts) >= 2 and parts[0].lower() == "in":
        return unquote(parts[1]).strip().lower()
    return ""


def name_key(name: str | None) -> str:
    """Accent/case/punctuation-insensitive full name; '' unless at least two words."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    words = "".join(c if c.isalnum() else " " for c in s).split()
    return " ".join(sorted(words)) if len(words) >= 2 else ""


class AttioIndex:
    """slugs: LinkedIn slugs of all Attio people. names: full names of Attio people that have NO
    LinkedIn URL (a person with a different LinkedIn URL is a different person, never a name match)."""

    def __init__(self, slugs: set[str], names: set[str]):
        self.slugs, self.names = slugs, names

    def __len__(self):
        return len(self.slugs) + len(self.names)

    def contains(self, name: str, profile_url: str) -> bool:
        slug = linkedin_slug(profile_url)
        if slug and slug in self.slugs:
            return True
        return bool(name_key(name) and name_key(name) in self.names)


def build_index(records: list[dict]) -> AttioIndex:
    slugs, names = set(), set()
    for rec in records:
        values = rec.get("values") or {}
        urls = [v.get("value") for v in values.get("linkedin") or [] if v.get("value")]
        slug_list = [s for s in (linkedin_slug(u) for u in urls) if s]
        slugs.update(slug_list)
        if not urls:
            full = " ".join(((values.get("name") or [{}])[0].get("full_name") or "").split())
            if name_key(full):
                names.add(name_key(full))
    return AttioIndex(slugs, names)


def load_index(api_key: str | None = None, sleep=None) -> AttioIndex:
    key = api_key or env("ATTIO_API_KEY")
    if not key:
        raise AttioError("ATTIO_API_KEY is not set")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    records, offset = [], 0
    while True:
        try:
            r = http("POST", f"{BASE}/objects/people/records/query", headers=headers,
                     json={"limit": PAGE, "offset": offset})
        except requests.RequestException as e:
            raise AttioError(f"Attio request failed: {e}") from e
        if r.status_code in (401, 403):
            raise AttioError(f"Attio auth failed (HTTP {r.status_code}): check ATTIO_API_KEY scopes")
        if r.status_code != 200:
            raise AttioError(f"Attio HTTP {r.status_code}: {r.text[:200]}")
        data = r.json().get("data") or []
        records += data
        if len(data) < PAGE:
            break
        offset += PAGE
    return build_index(records)
