#!/usr/bin/env python3
"""Score Status=New engagers against icp.md with Claude and write an icebreaker.
Score >= QUALIFY_MIN_SCORE -> Qualified, else Rejected."""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import notion as N  # noqa: E402
from lib.claude_client import Claude  # noqa: E402
from lib.config import QUALIFY_MIN_SCORE  # noqa: E402
from lib.notion import Notion  # noqa: E402
from lib.util import get_logger  # noqa: E402

log = get_logger("qualify_engagers")


def main() -> int:
    notion = Notion()
    rows = list(notion.query("engagers", filter={"property": "Status", "select": {"equals": "New"}}))
    if not rows:
        log.info("nothing to qualify")
        print("QUALIFIED=0")
        return 0

    post_ctx = {N.read(p, "Post URL"): {"author": N.read(p, "Author"), "text": N.read(p, "Excerpt")}
                for p in notion.query("posts")}
    by_post = defaultdict(list)
    for r in rows:
        by_post[N.read(r, "Post URL")].append(r)

    claude, qualified, rejected = Claude(), 0, 0
    for post_url, group in by_post.items():
        people = []
        for r in group:
            if "/company/" in N.read(r, "Profile URL"):
                notion.update(r["id"], {"Status": N.select("Rejected"), "Rationale": N.text("Company page")})
                rejected += 1
                continue
            people.append({"id": r["id"], "name": N.read(r, "Name"), "headline": N.read(r, "Headline"),
                           "action": N.read(r, "Source") + (f" ({N.read(r, 'Reaction Type')})" if N.read(r, "Reaction Type") else ""),
                           "comment": N.read(r, "Comment Text")})
        if not people:
            continue
        scores = claude.score_engagers(post_ctx.get(post_url, {"author": "", "text": ""}), people)
        for r in (x for x in group if x["id"] in {p["id"] for p in people}):
            s = scores.get(r["id"])
            if not s:  # model skipped this one: leave as New, retry next run
                continue
            score = int(s.get("score", 0))
            ok = score >= QUALIFY_MIN_SCORE
            notion.update(r["id"], {
                "Score": N.num(score), "Fit": N.select(s.get("fit")), "Rationale": N.text(s.get("rationale", "")),
                "Icebreaker": N.text(s.get("icebreaker", "")), "Status": N.select("Qualified" if ok else "Rejected")})
            qualified += ok
            rejected += not ok
    summary = f"{qualified} qualified, {rejected} rejected"
    log.info(summary)
    notion.log_run("qualify", "ok", summary)
    print(f"QUALIFIED={qualified}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
