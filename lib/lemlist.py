"""Minimal lemlist API client: find-or-create a campaign, add leads. Nothing is ever sent by this code
(a campaign without a sequence / not started sends nothing)."""
import time

from .config import env
from .util import http

API = "https://api.lemlist.com/api"
ALREADY = ("already", "duplicate", "exists")
NOT_PERSON = "not a personal profile"


class LemlistError(Exception):
    pass


class LemlistAuthError(LemlistError):
    pass


class Lemlist:
    def __init__(self, api_key: str | None = None, sleep=time.sleep):
        self.key = api_key or env("LEMLIST_API_KEY", required=True)
        self.sleep = sleep

    def _req(self, method: str, path: str, **kw):
        r = http(method, f"{API}{path}", auth=("", self.key), **kw)
        self.sleep(0.15)  # lemlist allows ~20 requests / 2 s
        if r.status_code in (401, 403):
            raise LemlistAuthError(f"lemlist HTTP {r.status_code}: check LEMLIST_API_KEY")
        return r

    def find_campaign(self, name: str) -> str:
        offset = 0
        while True:
            r = self._req("GET", "/campaigns", params={"version": "v2", "limit": 100, "page": offset // 100 + 1})
            if r.status_code != 200:
                raise LemlistError(f"lemlist list campaigns HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()
            items = data.get("campaigns", []) if isinstance(data, dict) else data
            for c in items:
                if (c.get("name") or "").strip().lower() == name.strip().lower():
                    return c.get("_id") or c.get("id") or ""
            if len(items) < 100:
                return ""
            offset += 100

    def create_campaign(self, name: str) -> str:
        r = self._req("POST", "/campaigns", json={"name": name})
        if r.status_code not in (200, 201):
            raise LemlistError(f"lemlist create campaign HTTP {r.status_code}: {r.text[:200]}")
        cid = r.json().get("_id") or r.json().get("id") or ""
        if not cid:
            raise LemlistError("lemlist create campaign: no id in response")
        return cid

    def get_or_create_campaign(self, name: str) -> tuple[str, bool]:
        cid = self.find_campaign(name)
        return (cid, False) if cid else (self.create_campaign(name), True)

    def add_lead(self, campaign_id: str, lead: dict) -> str:
        """Returns 'added', 'exists' or 'skipped' (LinkedIn URL is a company / not a person); raises LemlistError otherwise."""
        r = self._req("POST", f"/campaigns/{campaign_id}/leads/", json=lead)
        if r.status_code in (200, 201):
            return "added"
        if r.status_code == 400 and NOT_PERSON in r.text.lower():
            return "skipped"
        if r.status_code in (400, 409) and any(w in r.text.lower() for w in ALREADY):
            return "exists"
        raise LemlistError(f"lemlist add lead HTTP {r.status_code}: {r.text[:200]}")
