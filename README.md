# linkedin-monitor (Unipile + Notion)

Follows a watchlist of LinkedIn accounts (people and companies), stores all their posts in Notion, and records who reacted / commented on the recent ones.
No scoring, no LLM, no outreach tool. Notion is the database, so it runs fine on cron or GitHub Actions.

```
Notion Watchlist (accounts to follow)
  ─► Unipile: resolve provider id once, list each account's posts
  ─► Notion Posts (new posts created; counters of posts < 7 days old refreshed)
  ─► Unipile: reactions + comments of posts < 7 days old ─► Notion Engagers (de-duplicated)
  ─► Notion Run Log + 05-actions-log/results.md
```

## Setup

1. `pip install -r requirements.txt`; `cp .env.example .env` and fill it in.
2. Notion: create an integration, share a parent page with it, then `python setup_notion.py <parent_page_id>`
   and paste the four printed lines (`NOTION_WATCHLIST_DB`, `NOTION_POSTS_DB`, `NOTION_ENGAGERS_DB`, `NOTION_RUNS_DB`) into `.env`.
3. Fill the watchlist: add rows in Notion (LinkedIn URL + Status `Active`), or bulk-add from a text file:
   `python 03-workflows/add_to_watchlist.py accounts.txt` (one URL per line, optional `, label`; `#` comments allowed;
   duplicates and URLs that are not `/in/` or `/company/` are skipped).
4. Try it: `python monitor.py`.
5. Schedule daily: `0 6 * * * cd /path/linkedin-monitor && python3 monitor.py` (cron/launchd) or use
   `.github/workflows/monitor.yml` (secrets: `UNIPILE_DSN`, `UNIPILE_API_KEY`, `UNIPILE_ACCOUNT_ID`, `NOTION_TOKEN`,
   `NOTION_WATCHLIST_DB`, `NOTION_POSTS_DB`, `NOTION_ENGAGERS_DB`, `NOTION_RUNS_DB`).

## Notion databases

- **Watchlist**: Name (title), LinkedIn URL, Type (person/company), Status (Active/Paused), Provider ID, Last Checked, Last Error.
  Only Name/URL/Status need filling in; Provider ID, Type and Name are resolved on the first run. Set Status=Paused to skip an account.
- **Posts**: Title, Post URL, Social ID, Account, Account URL, Posted At, Posted (raw), Text, Reactions, Comments, Reposts,
  Is Repost, Collected At, Counters Updated.
- **Engagers**: Name, Profile URL, Headline, Post URL, Account, Source (Reaction/Comment), Reaction Type, Comment Text, Key, Collected At.
  One row per person per post (a person who both reacts and comments gets two rows); `Key` is the de-dup hash, don't edit it.
- **Run Log**: Run, Script, Status, Summary, At.

## Scripts

- `03-workflows/fetch_watchlist_posts.py` checks Unipile auth (exit 2 and nothing written on failure), then for each
  non-paused account lists up to `MAX_POSTS_PER_ACCOUNT` posts. Unseen posts are created; already-seen posts younger than
  `REFRESH_WINDOW_DAYS` only get their counters refreshed; older ones are left alone. A failing account sets its `Last Error`
  and the run continues; a 401/403 aborts the run with exit 2. Prints `NEW_POSTS=<n>` and exits 0 after any normal run.
- `03-workflows/fetch_engagers.py` visits posts younger than `ENGAGER_WINDOW_DAYS` and stores up to `MAX_REACTIONS_PER_POST` reactions and
  `MAX_COMMENTS_PER_POST` top-level comments each (replies to comments are not collected). Same exit-code rules as above; prints `NEW_ENGAGERS=<n>`.
  If `NOTION_ENGAGERS_DB` is not set it logs a warning and skips. It stops cleanly after `ENGAGER_TIME_BUDGET_S` (780 s) and
  visits the least-covered posts first, so the first big catch-up simply continues over the next runs (status `partial` in the Run Log).
- **Attio de-dup (optional):** if `ATTIO_API_KEY` is set, `fetch_engagers.py` loads all Attio People once per run and skips any engager
  already there (match on LinkedIn `/in/` slug; otherwise on full name, only against Attio people that have no LinkedIn URL).
  Skipped people are counted in the Run Log summary. If Attio is unreachable the run continues without de-dup and logs `partial`.
  Rows already in Notion are not touched.
- `03-workflows/push_to_lemlist.py` (only when `LEMLIST_API_KEY` **and** `ATTIO_API_KEY` are set) finds or creates the lemlist campaign
  `LEMLIST_CAMPAIGN_NAME` and adds every engager that is not in Attio as a lead (once per person). It creates no sequence and
  launches nothing. Each Notion Engagers row gets a `Lemlist` value (Pushed / In Attio / No URL); empty = still to do.
  Prints `PUSHED=<n>`. Needs a `Lemlist` select property on the Engagers database (`setup_notion.py` creates it).
- `03-workflows/add_to_watchlist.py <file>` bulk-adds accounts.
- `monitor.py` runs the two fetch scripts and then the lemlist push one after the other, each with a 900 s timeout and returns 1 on auth failure, timeout or non-zero exit.

Post URLs are stored exactly as Unipile returns them (`share_url`), with only the query string and trailing slash stripped.
Share / activity / ugcPost URLs are never converted into one another.

## Settings (optional env vars)

`MAX_POSTS_PER_ACCOUNT` (50), `REFRESH_WINDOW_DAYS` (7), `DELAY_MIN_S` / `DELAY_MAX_S` (3 / 8 seconds between accounts or posts),
`ENGAGER_WINDOW_DAYS` (7), `MAX_REACTIONS_PER_POST` (100), `MAX_COMMENTS_PER_POST` (100), `ENGAGER_TIME_BUDGET_S` (780).

## Notes

- Unipile reconnects: if auth fails, reconnect the LinkedIn account in Unipile.
- Use only with an account you are authorised to use, within LinkedIn's terms and GDPR. Keep `.env` out of git.
- Tests: `python -m unittest discover -s tests`
