"""Unipile client (LinkedIn via official-style REST, no browser).

Replaces the Playwright scraper. Auth = X-API-KEY header + account_id.
NOTE: Unipile exposes no 'list reposters' endpoint, so reposters are not collected.
"""
from typing import Iterator

from .config import env
from .models import Engager, Post
from .util import canonical_url, http, now_iso


class UnipileError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Unipile HTTP {status}: {message[:300]}")
        self.status = status


class AuthError(UnipileError):
    pass


# ---------------------------------------------------------------- parsing ----
def _profile_url(a: dict) -> str:
    for k in ("public_profile_url", "profile_url", "url"):
        if a.get(k):
            return a[k]
    if a.get("public_identifier"):
        return f"https://www.linkedin.com/in/{a['public_identifier']}"
    if a.get("id"):
        kind = "company" if a.get("is_company") else "in"
        return f"https://www.linkedin.com/{kind}/{a['id']}"
    return ""


def parse_post(item: dict, keyword: str) -> Post | None:
    social_id = item.get("social_id") or item.get("id") or ""
    # Preserve the URL exactly as LinkedIn/Unipile gives it.
    url = item.get("share_url") or (f"https://www.linkedin.com/feed/update/{social_id}" if social_id else "")
    if not url:
        return None
    author = item.get("author") or {}
    text = item.get("text") or ""
    return Post(
        post_url=canonical_url(url),
        social_id=social_id,
        post_date=item.get("parsed_datetime") or item.get("date") or "",
        post_author_name=author.get("name") or "",
        post_author_profile_url=_profile_url(author),
        matched_keyword=keyword,
        post_text_excerpt=" ".join(text.split())[:600],
        search_query=keyword,
        collected_at=now_iso(),
    )


def parse_reaction(item: dict, post: dict) -> Engager | None:
    a = item.get("author") or {}
    name = a.get("name") or ""
    if not name:
        return None
    return Engager(
        post_url=post["post_url"], post_author_name=post.get("author", ""),
        name=name, headline=a.get("headline") or "", linkedin_url=_profile_url(a),
        source="reaction", reaction_type=item.get("value") or "", collected_at=now_iso(),
    )


def parse_comment(item: dict, post: dict) -> Engager | None:
    a = item.get("author_details") or (item.get("author") if isinstance(item.get("author"), dict) else {}) or {}
    name = a.get("name") or (item.get("author") if isinstance(item.get("author"), str) else "") or ""
    if not name:
        return None
    return Engager(
        post_url=post["post_url"], post_author_name=post.get("author", ""),
        name=name, headline=a.get("headline") or "", linkedin_url=_profile_url(a),
        source="comment", comment_text=" ".join((item.get("text") or "").split()),
        collected_at=now_iso(),
    )


# ----------------------------------------------------------------- client ----
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

    def _paginate(self, method: str, path: str, *, params=None, json=None, limit: int) -> Iterator[dict]:
        got, cursor = 0, None
        while got < limit:
            p = {**(params or {}), "limit": min(100, limit - got)}
            if cursor:
                p["cursor"] = cursor
            data = self._req(method, path, p, json)
            items = data.get("items") or []
            for it in items:
                yield it
                got += 1
                if got >= limit:
                    return
            cursor = data.get("cursor")
            if not cursor or not items:
                return

    # -- auth gate: abort the run instead of recording false zeros ------------
    def check_auth(self) -> None:
        data = self._req("GET", f"/accounts/{self.account_id}")
        sources = data.get("sources") or []
        if sources and not any((s.get("status") or "").upper() == "OK" for s in sources):
            raise AuthError(401, f"LinkedIn account not healthy: {[s.get('status') for s in sources]}")

    def search_posts(self, keyword: str, date_posted: str, max_results: int) -> list[Post]:
        body = {"api": "classic", "category": "posts", "keywords": keyword,
                "sort_by": "date", "date_posted": date_posted}
        items = self._paginate("POST", "/linkedin/search", json=body, limit=max_results)
        posts = [p for it in items if (p := parse_post(it, keyword))]
        return posts

    def list_reactions(self, social_id: str, post: dict, limit: int) -> list[Engager]:
        out = self._paginate("GET", f"/posts/{social_id}/reactions", limit=limit)
        return [e for it in out if (e := parse_reaction(it, post))]

    def list_comments(self, social_id: str, post: dict, limit: int, include_replies: bool) -> list[Engager]:
        result: list[Engager] = []
        for it in self._paginate("GET", f"/posts/{social_id}/comments", limit=limit):
            if e := parse_comment(it, post):
                result.append(e)
            if include_replies and (it.get("reply_counter") or 0) > 0 and len(result) < limit:
                for rp in self._paginate("GET", f"/posts/{social_id}/comments",
                                         params={"comment_id": it.get("id")}, limit=limit - len(result)):
                    if e := parse_comment(rp, post):
                        result.append(e)
            if len(result) >= limit:
                break
        return result[:limit]
