"""Unipile client (LinkedIn via official-style REST, no browser).

Auth = X-API-KEY header + account_id query parameter.
"""
from typing import Iterator
from urllib.parse import quote

from .config import env
from .util import canonical_url, http


class UnipileError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Unipile HTTP {status}: {message[:300]}")
        self.status = status


class AuthError(UnipileError):
    pass


def _count(item: dict, key: str):
    v = item.get(key)
    return v if isinstance(v, int) else None


def _profile_url(a: dict) -> str:
    for k in ("public_profile_url", "profile_url", "url"):
        if a.get(k):
            return canonical_url(a[k])
    if a.get("public_identifier"):
        return f"https://www.linkedin.com/in/{a['public_identifier']}"
    return ""


def parse_reaction(item: dict) -> dict | None:
    """Unipile reaction item -> flat dict (parsed defensively: missing fields become '')."""
    a = item.get("author") if isinstance(item.get("author"), dict) else {}
    name = a.get("name") or ""
    if not name:
        return None
    return {"name": name, "headline": a.get("headline") or "", "profile_url": _profile_url(a),
            "source": "Reaction", "reaction_type": item.get("value") or "", "comment_text": ""}


def parse_comment(item: dict) -> dict | None:
    a = item.get("author_details") or (item.get("author") if isinstance(item.get("author"), dict) else {}) or {}
    name = a.get("name") or (item.get("author") if isinstance(item.get("author"), str) else "") or ""
    if not name:
        return None
    return {"name": name, "headline": a.get("headline") or "", "profile_url": _profile_url(a),
            "source": "Comment", "reaction_type": "", "comment_text": " ".join((item.get("text") or "").split())}


def parse_post(item: dict) -> dict | None:
    """Unipile post item -> flat dict. The permalink is preserved exactly (only the
    query string / trailing slash are stripped); share / activity / ugcPost URLs are
    never converted into one another."""
    social_id = item.get("social_id") or item.get("id") or ""
    url = item.get("share_url") or (f"https://www.linkedin.com/feed/update/{social_id}" if social_id else "")
    if not url:
        return None
    return {
        "url": canonical_url(url),
        "social_id": social_id,
        "text": item.get("text") or "",
        "date_raw": item.get("date") or "",
        "posted_at": item.get("parsed_datetime") or "",
        "reactions": _count(item, "reaction_counter"),
        "comments": _count(item, "comment_counter"),
        "reposts": _count(item, "repost_counter"),
        "is_repost": bool(item.get("is_repost")),
    }


class Unipile:
    def __init__(self, dsn: str | None = None, api_key: str | None = None, account_id: str | None = None):
        dsn = dsn or env("UNIPILE_DSN", required=True)  # e.g. api8.unipile.com:13851
        self.base = f"https://{dsn}/api/v1"
        self.headers = {"X-API-KEY": api_key or env("UNIPILE_API_KEY", required=True), "accept": "application/json"}
        self.account_id = account_id or env("UNIPILE_ACCOUNT_ID", required=True)

    def _req(self, method: str, path: str, params: dict | None = None, json: dict | None = None) -> dict:
        params = {"account_id": self.account_id, **(params or {})}
        r = http(method, f"{self.base}{path}", headers=self.headers, params=params, json=json)
        if r.status_code in (401, 403):
            raise AuthError(r.status_code, r.text)
        if r.status_code >= 400:
            raise UnipileError(r.status_code, r.text)
        return r.json()

    def _paginate(self, path: str, *, params: dict | None = None, limit: int) -> Iterator[dict]:
        got, cursor = 0, None
        while got < limit:
            p = {**(params or {}), "limit": min(100, limit - got)}
            if cursor:
                p["cursor"] = cursor
            data = self._req("GET", path, p)
            items = data.get("items") or []
            for it in items:
                yield it
                got += 1
                if got >= limit:
                    return
            cursor = data.get("cursor")
            if not cursor or not items:
                return

    # -- auth gate: abort the run instead of recording false results ----------
    def check_auth(self) -> None:
        data = self._req("GET", f"/accounts/{self.account_id}")
        sources = data.get("sources") or []
        if sources and not any((s.get("status") or "").upper() == "OK" for s in sources):
            raise AuthError(401, f"LinkedIn account not healthy: {[s.get('status') for s in sources]}")

    def list_reactions(self, social_id: str, limit: int) -> list[dict]:
        items = self._paginate(f"/posts/{quote(social_id, safe=':')}/reactions", limit=limit)
        return [e for it in items if (e := parse_reaction(it))]

    def list_comments(self, social_id: str, limit: int) -> list[dict]:
        items = self._paginate(f"/posts/{quote(social_id, safe=':')}/comments", limit=limit)
        return [e for it in items if (e := parse_comment(it))]

    def resolve_person(self, slug: str) -> dict:
        """GET /users/{slug} -> {'provider_id', 'name'}."""
        d = self._req("GET", f"/users/{quote(slug, safe='')}")
        pid = d.get("provider_id") or ""
        if not pid:
            raise UnipileError(404, f"no provider_id returned for person '{slug}'")
        name = " ".join(x for x in (d.get("first_name"), d.get("last_name")) if x)
        return {"provider_id": pid, "name": name}

    def resolve_company(self, slug: str) -> dict:
        """GET /linkedin/company/{slug} -> {'provider_id', 'name'}."""
        d = self._req("GET", f"/linkedin/company/{quote(slug, safe='')}")
        pid = d.get("id") or ""
        if not pid:
            raise UnipileError(404, f"no id returned for company '{slug}'")
        return {"provider_id": str(pid), "name": d.get("name") or ""}

    def list_posts(self, provider_id: str, is_company: bool, limit: int) -> list[dict]:
        params = {"is_company": "true"} if is_company else None
        return list(self._paginate(f"/users/{quote(provider_id, safe='')}/posts", params=params, limit=limit))
