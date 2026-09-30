#!/usr/bin/env python3
"""Discover new LinkedIn posts for the configured keywords (Unipile), filter them with
Claude, and store them in the Notion Posts database. Prints NEW_POSTS=<n>.

Exit codes: 0 = ok (even when posts were found), 2 = LinkedIn auth failure, 1 = other error.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import notion as N  # noqa: E402
from lib.claude_client import Claude  # noqa: E402
from lib.config import DATE_POSTED, KEYWORDS, LOG_DIR, SEARCH_MAX_RESULTS  # noqa: E402
from lib.notion import Notion  # noqa: E402
from lib.unipile import AuthError, Unipile  # noqa: E402
from lib.util import canonical_url, get_logger, now_iso  # noqa: E402

log = get_logger("search_linkedin_posts")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-filter", action="store_true", help="skip Claude relevance filter")
    args = ap.parse_args()

    uni, notion = Unipile(), Notion()
    try:
        uni.check_auth()
    except AuthError as e:
        log.error("LinkedIn auth failed, aborting: %s", e)
        notion.log_run("search", "auth_failed", str(e))
        print("NEW_POSTS=0")
        return 2

    # Dedup state lives in Notion: every URL / social id ever discovered.
    seen_urls, seen_ids = set(), set()
    for page in notion.query("posts"):
        seen_urls.add(canonical_url(N.read(page, "Post URL")))
        seen_ids.add(N.read(page, "Social ID"))

    new = []
    for kw in KEYWORDS:
        for post in uni.search_posts(kw, DATE_POSTED, SEARCH_MAX_RESULTS):
            if post.post_url in seen_urls or (post.social_id and post.social_id in seen_ids):
                continue
            seen_urls.add(post.post_url)
            seen_ids.add(post.social_id)
            new.append(post)
    log.info("search returned %d unseen posts", len(new))

    if new and not args.no_filter:
        verdicts = Claude().classify_posts(
            [{"id": p.post_url, "author": p.post_author_name, "text": p.post_text_excerpt} for p in new])
        for p in new:
            v = verdicts.get(p.post_url, {})
            p.relevant = bool(v.get("relevant", True))  # fail open: keep if the model skipped it
            p.relevance_reason = v.get("reason", "")
            p.post_language = v.get("language", "")
    else:
        for p in new:
            p.relevant = True

    relevant = 0
    md = []
    for p in new:
        notion.create("posts", {
            "Title": N.title(p.post_text_excerpt[:100] or p.post_url),
            "Post URL": N.url(p.post_url), "Social ID": N.text(p.social_id),
            "Author": N.text(p.post_author_name), "Author URL": N.url(p.post_author_profile_url),
            "Posted": N.text(p.post_date), "Language": N.select(p.post_language or None),
            "Keyword": N.text(p.matched_keyword), "Excerpt": N.text(p.post_text_excerpt),
            "Relevant": N.select("Yes" if p.relevant else "No"),
            "Relevance Reason": N.text(p.relevance_reason),
            "Status": N.select("Discovered" if p.relevant else "Irrelevant"),
            "Collected At": N.date(p.collected_at),
        })
        if p.relevant:
            relevant += 1
            md.append(f"- **{p.post_author_name}** ({p.post_date}) [{p.post_url}]({p.post_url})\n"
                      f"  > {p.post_text_excerpt[:200]}\n")

    if md:
        with open(LOG_DIR / "results.md", "a", encoding="utf-8") as f:
            f.write(f"\n## {now_iso()[:16]} - {relevant} new posts\n\n" + "\n".join(md))

    summary = f"{len(new)} unseen, {relevant} relevant"
    log.info(summary)
    notion.log_run("search", "ok", summary)
    print(f"NEW_POSTS={relevant}")
    return 0  # NOT len(new): a nonzero exit code would be read as failure by monitor.py


if __name__ == "__main__":
    sys.exit(main())
