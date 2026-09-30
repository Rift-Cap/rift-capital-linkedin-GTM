#!/usr/bin/env python3
"""Push engagers to the lemlist campaign.

REQUIRE_APPROVAL=true (default): pushes rows you set to Status=Approved in Notion.
REQUIRE_APPROVAL=false: pushes every Status=Qualified row automatically.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import notion as N  # noqa: E402
from lib.config import REQUIRE_APPROVAL  # noqa: E402
from lib.lemlist import Lemlist  # noqa: E402
from lib.notion import Notion  # noqa: E402
from lib.util import get_logger  # noqa: E402

log = get_logger("push_to_lemlist")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    notion = Notion()
    want = "Approved" if REQUIRE_APPROVAL else "Qualified"
    rows = list(notion.query("engagers", filter={"property": "Status", "select": {"equals": want}}))
    log.info("%d engagers with Status=%s", len(rows), want)
    lem = None if args.dry_run else Lemlist()
    counts = {"pushed": 0, "exists": 0, "error": 0}

    for r in rows:
        name, li = N.read(r, "Name"), N.read(r, "Profile URL")
        if not li:
            notion.update(r["id"], {"Status": N.select("Error"), "Error": N.text("No LinkedIn URL")})
            counts["error"] += 1
            continue
        if args.dry_run:
            log.info("[dry-run] would push %s %s", name, li)
            continue
        engagement = N.read(r, "Source") + (f":{N.read(r, 'Reaction Type')}" if N.read(r, "Reaction Type") else "")
        status, detail = lem.add_lead(
            name=name, linkedin_url=li, headline=N.read(r, "Headline"), icebreaker=N.read(r, "Icebreaker"),
            post_url=N.read(r, "Post URL"), engagement=engagement, score=int(N.read(r, "Score") or 0))
        counts[status] += 1
        notion.update(r["id"], {
            "Status": N.select({"pushed": "Pushed", "exists": "Already in lemlist", "error": "Error"}[status]),
            "Lemlist Lead ID": N.text(detail if status == "pushed" else ""),
            "Error": N.text(detail if status == "error" else "")})

    summary = f"pushed={counts['pushed']} exists={counts['exists']} errors={counts['error']}"
    log.info(summary)
    if not args.dry_run:
        notion.log_run("push", "ok" if not counts["error"] else "partial", summary)
    print(f"PUSHED={counts['pushed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
