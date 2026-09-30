#!/usr/bin/env python3
"""Collect reactors + commenters for eligible posts (Unipile) into the Notion Engagers DB.

  python get_post_engagement.py                  eligible posts only, capped
  python get_post_engagement.py --all            ignore age + last-scraped filters
  python get_post_engagement.py --all --no-cap   also ignore the per-run post cap

Reposters are not available via Unipile and are not collected.
A post that fails to load is marked with an error and NEVER recorded as zero engagement.
"""
import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import notion as N  # noqa: E402
from lib.config import (DELAY_MAX_S, DELAY_MIN_S, INCLUDE_REPLIES, MAX_POSTS_PER_RUN,  # noqa: E402
                        MIN_RESCRAPE_INTERVAL_HOURS, PER_CATEGORY_LIMIT, RESCRAPE_WINDOW_DAYS)
from lib.logic import select_eligible  # noqa: E402
from lib.notion import Notion  # noqa: E402
from lib.unipile import AuthError, Unipile, UnipileError  # noqa: E402
from lib.util import get_logger, now_iso, now_utc, parse_dt  # noqa: E402

log = get_logger("get_post_engagement")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-cap", action="store_true")
    args = ap.parse_args()

    uni, notion = Unipile(), Notion()
    try:
        uni.check_auth()
    except AuthError as e:
        log.error("LinkedIn auth failed, aborting without writing anything: %s", e)
        notion.log_run("engagement", "auth_failed", str(e))
        return 2

    pages = notion.query("posts", filter={"and": [
        {"property": "Relevant", "select": {"does_not_equal": "No"}},
        {"property": "Status", "select": {"does_not_equal": "Unavailable"}},
    ]})
    posts = [{
        "page_id": p["id"], "post_url": N.read(p, "Post URL"), "social_id": N.read(p, "Social ID"),
        "author": N.read(p, "Author"),
        "collected_at": parse_dt(N.read(p, "Collected At")), "last_scraped": parse_dt(N.read(p, "Last Scraped")),
    } for p in pages]

    todo = select_eligible(posts, now_utc(), RESCRAPE_WINDOW_DAYS, MIN_RESCRAPE_INTERVAL_HOURS,
                           ignore_filters=args.all, cap=None if args.no_cap else MAX_POSTS_PER_RUN)
    log.info("%d posts known, %d eligible this run", len(posts), len(todo))
    log.info("reposters: not supported by Unipile, skipped")

    new_total = errors = 0
    for i, post in enumerate(todo):
        if not post["social_id"]:
            log.warning("no social_id for %s, skipping", post["post_url"])
            continue
        if i:
            time.sleep(random.uniform(DELAY_MIN_S, DELAY_MAX_S))
        try:
            reactors = uni.list_reactions(post["social_id"], post, PER_CATEGORY_LIMIT)
            commenters = uni.list_comments(post["social_id"], post, PER_CATEGORY_LIMIT, INCLUDE_REPLIES)
        except AuthError as e:
            log.error("auth lost mid-run, stopping: %s", e)
            notion.log_run("engagement", "auth_failed", str(e))
            return 2
        except UnipileError as e:
            errors += 1
            gone = e.status in (404, 422)
            log.warning("post failed (%s): %s", post["post_url"], e)
            notion.update(post["page_id"], {"Last Error": N.text(str(e)),
                                            **({"Status": N.select("Unavailable")} if gone else {})})
            continue

        existing = {N.read(p, "Key") for p in notion.query(
            "engagers", filter={"property": "Post URL", "url": {"equals": post["post_url"]}})}
        added = 0
        for e in reactors + commenters:
            if e.key in existing:
                continue
            existing.add(e.key)
            notion.create("engagers", {
                "Name": N.title(e.name), "Profile URL": N.url(e.linkedin_url), "Headline": N.text(e.headline),
                "Post URL": N.url(e.post_url), "Post Author": N.text(e.post_author_name),
                "Source": N.select(e.source), "Reaction Type": N.select(e.reaction_type or None),
                "Comment Text": N.text(e.comment_text), "Key": N.text(e.key),
                "Status": N.select("New"), "First Seen": N.date(e.collected_at),
            })
            added += 1
        new_total += added

        ts = now_iso()
        notion.create("snapshots", {  # time series of engagement per post
            "Snapshot": N.title(f"{post['author']} {ts[:16]}"), "Post URL": N.url(post["post_url"]),
            "Reactions": N.num(len(reactors)), "Comments": N.num(len(commenters)),
            "New Engagers": N.num(added), "At": N.date(ts)})
        notion.update(post["page_id"], {
            "Last Scraped": N.date(ts), "Status": N.select("Scraped"), "Last Error": N.text(""),
            "Reactions": N.num(len(reactors)), "Comments": N.num(len(commenters))})
        log.info("%s: %d reactors, %d commenters, %d new", post["post_url"], len(reactors), len(commenters), added)

    summary = f"{len(todo)} posts scraped, {new_total} new engagers, {errors} errors"
    log.info(summary)
    notion.log_run("engagement", "ok" if not errors else "partial", summary)
    print(f"NEW_ENGAGERS={new_total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
