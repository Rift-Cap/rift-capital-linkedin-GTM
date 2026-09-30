#!/usr/bin/env python3
"""One-off: create the four Notion databases under a parent page.

1. Create an internal integration at notion.so/profile/integrations, copy its token into .env (NOTION_TOKEN).
2. Share a parent page with that integration (page ... > Connections).
3. python setup_notion.py <parent_page_id>   then paste the printed ids into .env
"""
import sys

from lib.notion import Notion

T, RT, U, NUM, D, S = {"title": {}}, {"rich_text": {}}, {"url": {}}, {"number": {}}, {"date": {}}, {"select": {}}

SCHEMAS = {
    "NOTION_POSTS_DB": ("LinkedIn Posts", {
        "Title": T, "Post URL": U, "Social ID": RT, "Author": RT, "Author URL": U, "Posted": RT,
        "Language": S, "Keyword": RT, "Excerpt": RT, "Relevant": S, "Relevance Reason": RT,
        "Status": S, "Collected At": D, "Last Scraped": D, "Reactions": NUM, "Comments": NUM, "Last Error": RT}),
    "NOTION_ENGAGERS_DB": ("LinkedIn Engagers", {
        "Name": T, "Profile URL": U, "Headline": RT, "Post URL": U, "Post Author": RT, "Source": S,
        "Reaction Type": S, "Comment Text": RT, "Score": NUM, "Fit": S, "Rationale": RT, "Icebreaker": RT,
        "Status": S, "First Seen": D, "Key": RT, "Lemlist Lead ID": RT, "Error": RT}),
    "NOTION_SNAPSHOTS_DB": ("Engagement Snapshots", {
        "Snapshot": T, "Post URL": U, "Reactions": NUM, "Comments": NUM, "New Engagers": NUM, "At": D}),
    "NOTION_RUNS_DB": ("Run Log", {"Run": T, "Script": S, "Status": S, "Summary": RT, "At": D}),
}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    notion = Notion()
    for env_name, (title, props) in SCHEMAS.items():
        print(f"{env_name}={notion.create_database(sys.argv[1], title, props)}")


if __name__ == "__main__":
    main()
