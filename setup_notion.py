#!/usr/bin/env python3
"""One-off: create the four Notion databases (watchlist, posts, engagers, run log) under a parent page.

1. Create an internal integration at notion.so/profile/integrations, copy its token into .env (NOTION_TOKEN).
2. Share a parent page with that integration (page ... > Connections).
3. python setup_notion.py <parent_page_id>   then paste the printed lines into .env
"""
import sys

from lib.notion import Notion

T, RT, U, NUM, D = {"title": {}}, {"rich_text": {}}, {"url": {}}, {"number": {}}, {"date": {}}


def S(*options: str) -> dict:
    return {"select": {"options": [{"name": o} for o in options]}}


SCHEMAS = {
    "NOTION_WATCHLIST_DB": ("LinkedIn Watchlist", {
        "Name": T, "LinkedIn URL": U, "Type": S("person", "company"), "Status": S("Active", "Paused"),
        "Provider ID": RT, "Last Checked": D, "Last Error": RT}),
    "NOTION_POSTS_DB": ("LinkedIn Posts", {
        "Title": T, "Post URL": U, "Social ID": RT, "Account": RT, "Account URL": U, "Posted At": D,
        "Posted (raw)": RT, "Text": RT, "Reactions": NUM, "Comments": NUM, "Reposts": NUM,
        "Is Repost": S("Yes", "No"), "Collected At": D, "Counters Updated": D}),
    "NOTION_ENGAGERS_DB": ("LinkedIn Engagers", {
        "Name": T, "Profile URL": U, "Headline": RT, "Post URL": U, "Account": RT,
        "Source": S("Reaction", "Comment"), "Reaction Type": RT, "Comment Text": RT, "Key": RT, "Collected At": D,
        "Lemlist": S("Pushed", "In Attio", "No URL")}),
    "NOTION_RUNS_DB": ("Run Log", {"Run": T, "Script": S(), "Status": S(), "Summary": RT, "At": D}),
}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    notion = Notion()
    for env_name, (title, props) in SCHEMAS.items():
        print(f"{env_name}={notion.create_database(sys.argv[1], title, props)}")


if __name__ == "__main__":
    main()
