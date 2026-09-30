#!/usr/bin/env python3
"""Fetch the posts of every account on the Notion Watchlist (Unipile) and store them in
the Notion Posts database. Prints NEW_POSTS=<n>.

Exit codes: 0 = normal run (always, even when posts were found), 2 = LinkedIn auth failure.
"""
import random
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from lib import notion as N  # noqa: E402
from lib.config import DELAY_MAX_S, DELAY_MIN_S, LOG_DIR, MAX_POSTS_PER_ACCOUNT, REFRESH_WINDOW_DAYS  # noqa: E402
from lib.notion import Notion  # noqa: E402
from lib.unipile import AuthError, Unipile, UnipileError, parse_post  # noqa: E402
from lib.util import canonical_url, get_logger, now_iso, now_utc, parse_account_url, parse_dt  # noqa: E402

SCRIPT = "fetch_watchlist_posts"
AUTH_FAILED = 2
log = get_logger(SCRIPT)


def load_seen(notion) -> dict[str, dict]:
    """Every post already in Notion, keyed by canonical URL."""
    seen = {}
    for page in notion.query("posts"):
        url = canonical_url(N.read(page, "Post URL"))
        if url:
            seen[url] = {"id": page["id"], "posted_at": N.read(page, "Posted At"),
                         "collected_at": N.read(page, "Collected At")}
    return seen


def load_watchlist(notion) -> list[dict]:
    return [p for p in notion.query("watchlist") if N.read(p, "Status") != "Paused"]


def resolve(uni, kind: str, slug: str) -> dict:
    return uni.resolve_person(slug) if kind == "person" else uni.resolve_company(slug)


def post_props(post: dict, account: str, account_url: str, collected_at: str) -> dict:
    text = " ".join(post["text"].split())
    return {
        "Title": N.title(text[:100] or post["url"]),
        "Post URL": N.url(post["url"]), "Social ID": N.text(post["social_id"]),
        "Account": N.text(account), "Account URL": N.url(account_url),
        "Posted At": N.date(post["posted_at"] or None), "Posted (raw)": N.text(post["date_raw"]),
        "Text": N.text(post["text"]),
        "Reactions": N.num(post["reactions"]), "Comments": N.num(post["comments"]),
        "Reposts": N.num(post["reposts"]),
        "Is Repost": N.select("Yes" if post["is_repost"] else "No"),
        "Collected At": N.date(collected_at), "Counters Updated": N.date(collected_at),
    }


def counter_props(post: dict, at: str) -> dict:
    return {"Reactions": N.num(post["reactions"]), "Comments": N.num(post["comments"]),
            "Reposts": N.num(post["reposts"]), "Counters Updated": N.date(at)}


def process_account(uni, notion, row: dict, seen: dict, now) -> list[dict]:
    """Returns the list of newly created posts. Raises UnipileError / requests errors."""
    url = canonical_url(N.read(row, "LinkedIn URL"))
    parsed = parse_account_url(url)
    if not parsed:
        raise ValueError(f"not a LinkedIn /in/ or /company/ URL: {url or '(empty)'}")
    kind, slug = parsed
    name = N.read(row, "Name") or slug

    provider_id = N.read(row, "Provider ID").strip()
    if not provider_id:
        info = resolve(uni, kind, slug)
        provider_id = info["provider_id"]
        props = {"Provider ID": N.text(provider_id), "Type": N.select(kind)}
        if info["name"]:
            name = info["name"]
            props["Name"] = N.title(name)
        notion.update(row["id"], props)

    items = uni.list_posts(provider_id, kind == "company", MAX_POSTS_PER_ACCOUNT)
    stamp = now_iso()
    window = timedelta(days=REFRESH_WINDOW_DAYS)
    created = []
    for item in items:
        post = parse_post(item)
        if not post:
            continue
        old = seen.get(post["url"])
        if old is None:
            page = notion.create("posts", post_props(post, name, url, stamp))
            seen[post["url"]] = {"id": page.get("id", ""), "posted_at": post["posted_at"], "collected_at": stamp}
            created.append(post)
            continue
        ref = parse_dt(post["posted_at"]) or parse_dt(old["posted_at"]) or parse_dt(old["collected_at"])
        if ref and now - ref < window:
            notion.update(old["id"], counter_props(post, stamp))
    return created


def write_results_md(new_by_account: dict[str, list[dict]], errors: list[str], status: str) -> None:
    total = sum(len(v) for v in new_by_account.values())
    lines = [f"\n## {now_iso()[:16]} - {total} new posts ({status})\n"]
    for account, posts in new_by_account.items():
        if not posts:
            continue
        lines.append(f"\n### {account}\n")
        for p in posts:
            excerpt = " ".join(p["text"].split())[:200]
            lines.append(f"- ({p['date_raw'] or p['posted_at'] or 'undated'}) [{p['url']}]({p['url']})\n  > {excerpt}\n")
    if errors:
        lines.append("\n### Errors\n")
        lines += [f"- {e}\n" for e in errors]
    with open(LOG_DIR / "results.md", "a", encoding="utf-8") as f:
        f.write("".join(lines))


def run(uni, notion, sleep=time.sleep, now=None) -> tuple[int, int]:
    """Returns (exit_code, new_post_count)."""
    try:
        uni.check_auth()
    except AuthError as e:
        log.error("LinkedIn auth failed, aborting: %s", e)
        return AUTH_FAILED, 0

    now = now or now_utc()
    seen = load_seen(notion)
    rows = load_watchlist(notion)
    log.info("%d active accounts, %d known posts", len(rows), len(seen))

    new_by_account: dict[str, list[dict]] = {}
    errors: list[str] = []
    code, ok = 0, 0
    for i, row in enumerate(rows):
        if i:
            sleep(random.uniform(DELAY_MIN_S, DELAY_MAX_S))
        label = N.read(row, "Name") or N.read(row, "LinkedIn URL") or row.get("id", "?")
        try:
            created = process_account(uni, notion, row, seen, now)
        except AuthError as e:
            log.error("auth error on %s, aborting run: %s", label, e)
            errors.append(f"{label}: auth error, run aborted ({e})")
            code = AUTH_FAILED
            break
        except (UnipileError, requests.RequestException, ValueError) as e:
            log.warning("%s failed: %s", label, e)
            errors.append(f"{label}: {e}")
            notion.update(row["id"], {"Last Error": N.text(str(e))})
            continue
        new_by_account[label] = created
        ok += 1
        notion.update(row["id"], {"Last Checked": N.date(now_iso()), "Last Error": N.text("")})

    new_count = sum(len(v) for v in new_by_account.values())
    status = "auth_failed" if code == AUTH_FAILED else ("partial" if errors else "ok")
    summary = f"{ok}/{len(rows)} accounts ok, {new_count} new posts, {len(errors)} errors"
    if errors:
        summary += ": " + "; ".join(errors)[:800]
    log.info(summary)
    write_results_md(new_by_account, errors, status)
    notion.log_run(SCRIPT, status, summary)
    return code, new_count


def main() -> int:
    code, new_count = run(Unipile(), Notion())
    print(f"NEW_POSTS={new_count}")
    return code  # 0 after a normal run, never len(new_posts): nonzero reads as failure


if __name__ == "__main__":
    sys.exit(main())
