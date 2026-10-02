#!/usr/bin/env python3
"""Put engagers that are NOT in Attio into a lemlist campaign (found by name, created on first use).

Only the campaign and its leads are created: no sequence, nothing is launched or sent.
Each Notion Engagers row gets a `Lemlist` value: Pushed / In Attio / No URL (rows left empty are retried next run).
A person who engaged several times is pushed once. Needs LEMLIST_API_KEY and ATTIO_API_KEY (without Attio the
step refuses to run, so known contacts are never pushed). Prints PUSHED=<n>.
Exit codes: 0 = normal run (also when skipped), 1 = errors / lemlist or Attio failure.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import attio as A  # noqa: E402
from lib import notion as N  # noqa: E402
from lib.config import ENGAGER_TIME_BUDGET_S, LEMLIST_CAMPAIGN_NAME, env  # noqa: E402
from lib.lemlist import Lemlist, LemlistError  # noqa: E402
from lib.notion import Notion  # noqa: E402
from lib.util import get_logger  # noqa: E402

SCRIPT = "push_to_lemlist"
log = get_logger(SCRIPT)


def split_name(full: str) -> tuple[str, str]:
    parts = (full or "").split()
    return (parts[0], " ".join(parts[1:])) if parts else ("", "")


def person_key(row_url: str, name: str) -> str:
    return A.linkedin_slug(row_url) or (row_url or "").strip().lower()


def run(notion, lemlist, attio, campaign_name=None, clock=time.monotonic, budget_s=None) -> tuple[int, int]:
    """Returns (exit_code, pushed_count)."""
    deadline = clock() + (ENGAGER_TIME_BUDGET_S if budget_s is None else budget_s)
    rows = list(notion.query("engagers"))
    people: dict[str, dict] = {}
    for row in rows:
        if N.read(row, "Lemlist"):
            continue
        url, name = N.read(row, "Profile URL"), N.read(row, "Name")
        key = person_key(url, name)
        if not key:
            notion.update(row["id"], {"Lemlist": N.select("No URL")})
            continue
        p = people.setdefault(key, {"url": url, "name": name, "headline": N.read(row, "Headline"), "rows": []})
        p["rows"].append(row["id"])
    log.info("%d engager rows, %d people to check", len(rows), len(people))

    campaign_id, errors, pushed, in_attio, out_of_time = "", [], 0, 0, False
    for p in people.values():
        if clock() >= deadline:
            out_of_time = True
            break
        if attio.contains(p["name"], p["url"]):
            for rid in p["rows"]:
                notion.update(rid, {"Lemlist": N.select("In Attio")})
            in_attio += 1
            continue
        try:
            if not campaign_id:
                campaign_id, created = lemlist.get_or_create_campaign(campaign_name or LEMLIST_CAMPAIGN_NAME)
                log.info("lemlist campaign %s (%s)", campaign_id, "created" if created else "existing")
            first, last = split_name(p["name"])
            lemlist.add_lead(campaign_id, {"linkedinUrl": p["url"], "firstName": first, "lastName": last,
                                           "jobTitle": p["headline"][:200]})
        except LemlistError as e:
            log.warning("%s: %s", p["name"], e)
            errors.append(f"{p['name']}: {e}")
            if "check LEMLIST_API_KEY" in str(e):
                break
            continue
        for rid in p["rows"]:
            notion.update(rid, {"Lemlist": N.select("Pushed")})
        pushed += 1

    status = "partial" if errors or out_of_time else "ok"
    summary = f"{pushed} pushed to lemlist campaign '{campaign_name or LEMLIST_CAMPAIGN_NAME}', {in_attio} already in Attio, {len(errors)} errors"
    if out_of_time:
        summary += ", time budget reached (resuming next run)"
    if errors:
        summary += ": " + "; ".join(errors)[:600]
    log.info(summary)
    notion.log_run(SCRIPT, status, summary)
    return (1 if errors else 0), pushed


def main() -> int:
    if not env("LEMLIST_API_KEY"):
        log.warning("LEMLIST_API_KEY is not set: skipping lemlist push")
        print("PUSHED=0")
        return 0
    notion = Notion()
    if not notion.dbs.get("engagers"):
        log.warning("NOTION_ENGAGERS_DB is not set: skipping lemlist push")
        print("PUSHED=0")
        return 0
    try:
        attio = A.load_index()
    except A.AttioError as e:
        log.error("Attio unavailable, not pushing anything to lemlist: %s", e)
        notion.log_run(SCRIPT, "failed", f"Attio unavailable, nothing pushed: {e}")
        print("PUSHED=0")
        return 1
    code, pushed = run(notion, Lemlist(), attio)
    print(f"PUSHED={pushed}")
    return code


if __name__ == "__main__":
    sys.exit(main())
