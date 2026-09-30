#!/usr/bin/env python3
"""Bulk-add LinkedIn accounts to the Notion Watchlist.

python 03-workflows/add_to_watchlist.py accounts.txt

One URL per line, optional label after a comma:
    https://www.linkedin.com/in/jane-doe, Jane Doe
    https://www.linkedin.com/company/acme
Blank lines and lines starting with # are ignored. Duplicates and URLs that are not
/in/ or /company/ profiles are skipped.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import notion as N  # noqa: E402
from lib.notion import Notion  # noqa: E402
from lib.util import canonical_url, get_logger, parse_account_url  # noqa: E402

log = get_logger("add_to_watchlist")


def parse_lines(lines) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    """-> ([(canonical_url, type, slug, label)], skipped_lines). Duplicates inside the file are dropped."""
    out, skipped, seen = [], [], set()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        url, _, label = line.partition(",")
        parsed = parse_account_url(url)
        if not parsed:
            skipped.append(line)
            continue
        canon = canonical_url(url if "://" in url else "https://" + url.strip())
        if canon.lower() in seen:
            continue
        seen.add(canon.lower())
        out.append((canon, parsed[0], parsed[1], label.strip()))
    return out, skipped


def add(notion, lines) -> tuple[int, int, int]:
    """-> (added, duplicates, invalid)"""
    entries, skipped = parse_lines(lines)
    existing = {canonical_url(N.read(p, "LinkedIn URL")).lower() for p in notion.query("watchlist")}
    added = dupes = 0
    for canon, kind, slug, label in entries:
        if canon.lower() in existing:
            dupes += 1
            continue
        notion.create("watchlist", {
            "Name": N.title(label or slug), "LinkedIn URL": N.url(canon),
            "Type": N.select(kind), "Status": N.select("Active"),
        })
        existing.add(canon.lower())
        added += 1
    for s in skipped:
        log.warning("skipped (not an /in/ or /company/ URL): %s", s)
    return added, dupes, len(skipped)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
    added, dupes, invalid = add(Notion(), lines)
    log.info("added %d, duplicates %d, invalid %d", added, dupes, invalid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
