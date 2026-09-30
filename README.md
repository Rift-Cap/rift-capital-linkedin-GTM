# linkedin-monitor (Unipile + Claude + Notion + lemlist)

Finds LinkedIn posts for your keywords, collects who reacted/commented, scores them against your ICP with Claude,
and pushes approved leads into a lemlist campaign. Notion is the database, so it runs fine on cron or GitHub Actions.

```
Unipile search ─► Claude relevance filter ─► Notion Posts
Unipile reactions/comments (7-day rescrape window) ─► Notion Engagers + Snapshots
Claude score + icebreaker ─► Status Qualified/Rejected
You set Status=Approved in Notion ─► lemlist campaign lead (status Pushed)
```

## Setup
1. `pip install -r requirements.txt`; `cp .env.example .env` and fill it in.
2. Notion: create an integration, share a parent page with it, then `python setup_notion.py <parent_page_id>`
   and paste the four printed ids into `.env`.
3. Edit `icp.md` (topic, ICP, tone). This is Claude's whole context.
4. In lemlist, create the campaign, put `{{icebreaker}}` in step 1, and copy its id to `LEMLIST_CAMPAIGN_ID`.
5. Try it: `python 03-workflows/search_linkedin_posts.py`, then `python monitor.py`.
6. Schedule (~72h): `0 6 */3 * * cd /path/linkedin-monitor && python3 monitor.py` (cron/launchd) or use `.github/workflows/monitor.yml`.

## Scripts
- `search_linkedin_posts.py` discover, filter, store. Prints `NEW_POSTS=<n>`, always exits 0 (2 = auth failure).
- `get_post_engagement.py [--all] [--no-cap]` eligible = collected <=7d ago and not scraped in 24h, max 15/run.
- `qualify_engagers.py` Claude scoring, threshold `QUALIFY_MIN_SCORE` (70).
- `push_to_lemlist.py [--dry-run]` pushes `Approved` rows (or `Qualified` if `REQUIRE_APPROVAL=false`).
- `monitor.py` runs the four in order, 900s timeout each, stops on auth failure.

## Differences from the Playwright original
- No browser, no session file. Unipile holds the LinkedIn session; reconnect the account in Unipile if auth fails.
- **Reposters are not collected**: Unipile has no endpoint for them.
- Permalinks come from Unipile's `share_url` as-is (query/trailing slash stripped); no clipboard trick needed.
- CSVs and JSON state are replaced by Notion. Engagers are de-duplicated per post (reactions: profile; comments:
  profile + first 80 chars). The time series lives in the Snapshots database (counts per scrape).
- `monitor.py` always runs engagement (needed for rescrapes), not only when new posts were found.
- Failed posts are flagged in `Last Error` and never recorded as zero engagement.

## Notes
- Unipile reaction/comment payloads are parsed defensively; check one real run and adjust `lib/unipile.py` parsers if a field differs.
- Use only with an account you are authorised to use, within LinkedIn's terms and GDPR. Keep `.env` out of git.
- Tests: `python -m unittest discover -s tests`
