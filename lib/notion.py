"""Minimal Notion REST client (API version 2022-06-28) used as the system's database.

Three databases: watchlist (accounts to follow), posts, runs (run log).
State lives in Notion so the job can run on ephemeral machines (cron, GitHub Actions).
"""
import time
from typing import Iterator

from .config import env
from .util import http, now_iso

API = "https://api.notion.com/v1"
TEXT_MAX = 1900  # Notion rich_text content limit is 2000


# ---- property builders -------------------------------------------------------
def title(v: str) -> dict:
    return {"title": [{"text": {"content": (v or "")[:TEXT_MAX]}}]}


def text(v: str) -> dict:
    return {"rich_text": [{"text": {"content": (v or "")[:TEXT_MAX]}}] if v else []}


def url(v: str) -> dict:
    return {"url": v or None}


def num(v) -> dict:
    return {"number": v}


def date(v: str | None) -> dict:
    return {"date": {"start": v} if v else None}


def select(v: str | None) -> dict:
    return {"select": {"name": v} if v else None}


# ---- property readers --------------------------------------------------------
def read(page: dict, name: str):
    prop = page.get("properties", {}).get(name) or {}
    t = prop.get("type")
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in prop[t])
    if t == "url":
        return prop["url"] or ""
    if t == "select":
        return (prop["select"] or {}).get("name", "")
    if t == "date":
        return (prop["date"] or {}).get("start") or ""
    if t == "number":
        return prop["number"]
    return ""


class Notion:
    def __init__(self, token: str | None = None):
        self.headers = {
            "Authorization": f"Bearer {token or env('NOTION_TOKEN', required=True)}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        self.dbs = {
            "watchlist": env("NOTION_WATCHLIST_DB"), "posts": env("NOTION_POSTS_DB"),
            "runs": env("NOTION_RUNS_DB"),
        }

    def _req(self, method: str, path: str, json: dict | None = None) -> dict:
        r = http(method, f"{API}{path}", headers=self.headers, json=json)
        if r.status_code >= 400:
            raise RuntimeError(f"Notion HTTP {r.status_code} on {path}: {r.text[:300]}")
        time.sleep(0.35)  # stay under Notion's ~3 req/s average limit
        return r.json()

    def db(self, key: str) -> str:
        if not self.dbs.get(key):
            raise SystemExit(f"Notion database id for '{key}' is not configured. Run setup_notion.py.")
        return self.dbs[key]

    def query(self, key: str, filter: dict | None = None, sorts: list | None = None) -> Iterator[dict]:
        body: dict = {"page_size": 100}
        if filter:
            body["filter"] = filter
        if sorts:
            body["sorts"] = sorts
        while True:
            data = self._req("POST", f"/databases/{self.db(key)}/query", body)
            yield from data["results"]
            if not data.get("has_more"):
                return
            body["start_cursor"] = data["next_cursor"]

    def create(self, key: str, props: dict) -> dict:
        return self._req("POST", "/pages", {"parent": {"database_id": self.db(key)}, "properties": props})

    def update(self, page_id: str, props: dict) -> dict:
        return self._req("PATCH", f"/pages/{page_id}", {"properties": props})

    def create_database(self, parent_page_id: str, name: str, props: dict) -> str:
        data = self._req("POST", "/databases", {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": name}}],
            "properties": props,
        })
        return data["id"]

    def log_run(self, name: str, status: str, summary: str) -> None:
        if not self.dbs.get("runs"):
            return
        try:
            self.create("runs", {"Run": title(f"{name} {now_iso()[:16]}"), "Script": select(name),
                                 "Status": select(status), "Summary": text(summary), "At": date(now_iso())})
        except Exception:  # logging must never break a run
            pass
