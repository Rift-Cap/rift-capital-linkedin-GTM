#!/usr/bin/env python3
"""Collect who reacted / commented on the recent posts stored in Notion (Unipile) and store
them in the Notion Engagers database. Prints NEW_ENGAGERS=<n>.

Only posts younger than ENGAGER_WINDOW_DAYS are visited (default 7), at most
MAX_REACTIONS_PER_POST reactions and MAX_COMMENTS_PER_POST comments per post.
The run stops cleanly after ENGAGER_TIME_BUDGET_S seconds (default 780, below monitor.py's 900 s timeout);
posts with the fewest stored engagers go first, so unfinished work resumes on the next run.
Exit codes: 0 = normal run (always, even when engagers were found or the time budget was hit),
2 = LinkedIn auth failure.
"""
import hashlib
import random
import sys
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from lib import notion as N  # noqa: E402
from lib.config import (DELAY_MAX_S, DELAY_MIN_S, ENGAGER_TIME_BUDGET_S, ENGAGER_WINDOW_DAYS,  # noqa: E402
                        LOG_DIR, MAX_COMMENTS_PER_POST, MAX_REACTIONS_PER_POST)
from lib.notion import Notion  # noqa: E402
from lib.unipile import AuthError, Unipile, UnipileError  # noqa: E402
from lib.util import canonical_url, get_logger, now_iso, now_utc, parse_dt  # noqa: E402

SCRIPT = "fetch_engagers"
AUTH_FAILED = 2
log = get_logger(SCRIPT)


def engager_key(post_url: str, e: dict) -> str:
    """Stable de-dup key: reactions = post + person; comments = post + person + first 80 chars."""
    ident = (e["profile_url"] or e["name"]).strip().lower()
    extra = e["comment_text"][:80].lower() if e["source"] == "Comment" else ""
    return hashlib.sha1(f"{post_url}|{e['source']}|{ident}|{extra}".encode("utf-8")).hexdigest()


def load_recent_posts(notion, now) -> list[dict]:
    window = timedelta(days=ENGAGER_WINDOW_DAYS)
    posts = []
    for page in notion.query("posts"):
        social_id = N.read(page, "Social ID").strip()
        url = canonical_url(N.read(page, "Post URL"))
        ref = parse_dt(N.read(page, "Posted At")) or parse_dt(N.read(page, "Collected At"))
        if not social_id or not url or not ref or now - ref >= window:
            continue
        posts.append({"social_id": social_id, "url": url, "account": N.read(page, "Account")})
    return posts


def engager_props(post: dict, e: dict, key: str, at: str) -> dict:
    return {
        "Name": N.title(e["name"]), "Profile URL": N.url(e["profile_url"]),
        "Headline": N.text(e["headline"]), "Post URL": N.url(post["url"]),
        "Account": N.text(post["account"]), "Source": N.select(e["source"]),
        "Reaction Type": N.text(e["reaction_type"]), "Comment Text": N.text(e["comment_text"]),
        "Key": N.text(key), "Collected At": N.date(at),
    }


def write_results_md(counts: dict[str, int], errors: list[str], status: str) -> None:
    total = sum(counts.values())
    lines = [f"\n## {now_iso()[:16]} - {total} new engagers ({status})\n"]
    for url, n in counts.items():
        if n:
            lines.append(f"- {n} on [{url}]({url})\n")
    if errors:
        lines.append("\n### Errors\n")
        lines += [f"- {e}\n" for e in errors]
    with open(LOG_DIR / "results.md", "a", encoding="utf-8") as f:
        f.write("".join(lines))


def run(uni, notion, sleep=time.sleep, now=None, clock=time.monotonic, budget_s=None) -> tuple[int, int]:
    """Returns (exit_code, new_engager_count)."""
    try:
        uni.check_auth()
    except AuthError as e:
        log.error("LinkedIn auth failed, aborting: %s", e)
        return AUTH_FAILED, 0

    deadline = clock() + (ENGAGER_TIME_BUDGET_S if budget_s is None else budget_s)
    now = now or now_utc()
    posts = load_recent_posts(notion, now)
    seen, known = set(), Counter()
    for row in notion.query("engagers"):
        key = N.read(row, "Key")
        if key:
            seen.add(key)
            known[canonical_url(N.read(row, "Post URL"))] += 1
    posts.sort(key=lambda p: known[p["url"]])  # least-covered first: resumes unfinished work
    log.info("%d recent posts, %d known engagers", len(posts), len(seen))

    counts: dict[str, int] = {}
    errors: list[str] = []
    code, out_of_time = 0, False
    stamp = now_iso()
    for i, post in enumerate(posts):
        if clock() >= deadline:
            out_of_time = True
            break
        if i:
            sleep(random.uniform(DELAY_MIN_S, DELAY_MAX_S))
        try:
            people = (uni.list_reactions(post["social_id"], MAX_REACTIONS_PER_POST)
                      + uni.list_comments(post["social_id"], MAX_COMMENTS_PER_POST))
        except AuthError as e:
            log.error("auth error on %s, aborting run: %s", post["url"], e)
            errors.append(f"{post['url']}: auth error, run aborted ({e})")
            code = AUTH_FAILED
            break
        except (UnipileError, requests.RequestException) as e:
            log.warning("%s failed: %s", post["url"], e)
            errors.append(f"{post['url']}: {e}")
            continue
        n = 0
        for e in people:
            if clock() >= deadline:  # a post is picked up again next run; the de-dup keys skip what is stored
                out_of_time = True
                break
            key = engager_key(post["url"], e)
            if key in seen:
                continue
            notion.create("engagers", engager_props(post, e, key, stamp))
            seen.add(key)
            n += 1
        counts[post["url"]] = n
        if out_of_time:
            break

    new_count = sum(counts.values())
    left = len(posts) - len(counts)
    status = "auth_failed" if code == AUTH_FAILED else ("partial" if errors or out_of_time else "ok")
    summary = f"{len(counts)}/{len(posts)} posts ok, {new_count} new engagers, {len(errors)} errors"
    if out_of_time:
        summary += f", time budget reached ({left} posts left or unfinished, resuming next run)"
    if errors:
        summary += ": " + "; ".join(errors)[:800]
    log.info(summary)
    write_results_md(counts, errors, status)
    notion.log_run(SCRIPT, status, summary)
    return code, new_count


def main() -> int:
    notion = Notion()
    if not notion.dbs.get("engagers"):
        log.warning("NOTION_ENGAGERS_DB is not set: skipping engagers collection")
        print("NEW_ENGAGERS=0")
        return 0
    code, new_count = run(Unipile(), notion)
    print(f"NEW_ENGAGERS={new_count}")
    return code  # 0 after a normal run


if __name__ == "__main__":
    sys.exit(main())
