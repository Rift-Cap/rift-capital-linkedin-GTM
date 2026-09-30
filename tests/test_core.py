import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import monitor
from lib import notion as N
from lib.unipile import AuthError, UnipileError, parse_post
from lib.util import canonical_url, parse_account_url


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "03-workflows" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch = load("fetch_watchlist_posts")
add_wl = load("add_to_watchlist")
NOW = datetime.now(timezone.utc)


def iso(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


class FakeNotion:
    """In-memory Notion: stores pages in the API's read format so N.read works."""

    def __init__(self, watchlist=(), posts=()):
        self.pages = {"watchlist": [], "posts": [], "runs": []}
        self.n = 0
        for props in watchlist:
            self.create("watchlist", props)
        for props in posts:
            self.create("posts", props)
        self.writes = 0  # counts writes after seeding
        self.log_calls = []

    @staticmethod
    def _convert(props):
        out = {}
        for k, v in props.items():
            t = next(iter(v))
            val = v[t]
            if t in ("title", "rich_text"):
                val = [{"plain_text": x["text"]["content"]} for x in val]
            out[k] = {"type": t, t: val}
        return out

    def create(self, key, props):
        self.n += 1
        page = {"id": f"p{self.n}", "properties": self._convert(props)}
        self.pages[key].append(page)
        if hasattr(self, "writes"):
            self.writes += 1
        return page

    def update(self, page_id, props):
        self.writes += 1
        for pages in self.pages.values():
            for p in pages:
                if p["id"] == page_id:
                    p["properties"].update(self._convert(props))
                    return p
        raise KeyError(page_id)

    def query(self, key, filter=None, sorts=None):
        return iter(list(self.pages[key]))

    def log_run(self, name, status, summary):
        self.writes += 1
        self.log_calls.append((name, status, summary))

    def row(self, key, i=0):
        return self.pages[key][i]


def wl(url, name="Acme", status="Active", provider="", **extra):
    return {"Name": N.title(name), "LinkedIn URL": N.url(url), "Status": N.select(status),
            "Provider ID": N.text(provider), **extra}


def item(i, **kw):
    d = {"social_id": f"urn:li:activity:{i}", "share_url": f"https://www.linkedin.com/posts/p-activity-{i}-q?utm=1",
         "text": f"post {i}", "date": "2d", "parsed_datetime": iso(days=2),
         "reaction_counter": 10, "comment_counter": 2, "repost_counter": 1, "is_repost": False}
    d.update(kw)
    return d


class FakeUnipile:
    def __init__(self, posts=None, auth_error=None, fail=None):
        self.posts = posts or {}          # provider_id -> items
        self.auth_error = auth_error
        self.fail = fail or {}            # provider_id -> exception
        self.resolved = []
        self.listed = []

    def check_auth(self):
        if self.auth_error:
            raise self.auth_error

    def resolve_person(self, slug):
        self.resolved.append(("person", slug))
        return {"provider_id": f"ACo-{slug}", "name": slug.title()}

    def resolve_company(self, slug):
        self.resolved.append(("company", slug))
        return {"provider_id": f"co-{slug}", "name": slug.title()}

    def list_posts(self, provider_id, is_company, limit):
        self.listed.append((provider_id, is_company, limit))
        if provider_id in self.fail:
            raise self.fail[provider_id]
        return self.posts.get(provider_id, [])


def run_fetch(uni, notion):
    with mock.patch.object(fetch, "write_results_md") as md:
        code, n = fetch.run(uni, notion, sleep=lambda s: None, now=NOW)
    return code, n, md


class UrlTests(unittest.TestCase):
    def test_canonical_keeps_permalink_type(self):
        self.assertEqual(canonical_url("https://www.linkedin.com/posts/a-share-99-xy/?utm=1#f"),
                         "https://www.linkedin.com/posts/a-share-99-xy")
        self.assertEqual(canonical_url("https://www.linkedin.com/feed/update/urn:li:ugcPost:5/"),
                         "https://www.linkedin.com/feed/update/urn:li:ugcPost:5")
        self.assertEqual(canonical_url("https://www.linkedin.com/feed/update/urn:li:activity:7?x=1"),
                         "https://www.linkedin.com/feed/update/urn:li:activity:7")
        self.assertEqual(canonical_url(None), "")

    def test_parse_post_preserves_url_and_fallback(self):
        p = parse_post(item(1))
        self.assertEqual(p["url"], "https://www.linkedin.com/posts/p-activity-1-q")
        self.assertEqual((p["reactions"], p["comments"], p["reposts"], p["is_repost"]), (10, 2, 1, False))
        fb = parse_post({"social_id": "urn:li:ugcPost:7"})
        self.assertEqual(fb["url"], "https://www.linkedin.com/feed/update/urn:li:ugcPost:7")
        self.assertIsNone(parse_post({"text": "no id"}))

    def test_parse_account_url(self):
        self.assertEqual(parse_account_url("https://www.linkedin.com/in/jane-doe/"), ("person", "jane-doe"))
        self.assertEqual(parse_account_url("linkedin.com/company/acme?x=1"), ("company", "acme"))
        self.assertEqual(parse_account_url("https://fr.linkedin.com/in/jean/fr"), ("person", "jean"))
        for bad in ("", None, "https://www.linkedin.com/feed/", "https://www.linkedin.com/in/",
                    "https://example.com/in/jane", "https://www.linkedin.com/posts/x-activity-1"):
            self.assertIsNone(parse_account_url(bad), bad)


class FetchTests(unittest.TestCase):
    def test_unseen_post_created_and_resolution_stored(self):
        nt = FakeNotion(watchlist=[wl("https://www.linkedin.com/in/jane")])
        uni = FakeUnipile({"ACo-jane": [item(1), item(2)]})
        code, n, md = run_fetch(uni, nt)
        self.assertEqual((code, n), (0, 2))
        self.assertEqual(len(nt.pages["posts"]), 2)
        row = nt.row("watchlist")
        self.assertEqual(N.read(row, "Provider ID"), "ACo-jane")
        self.assertEqual(N.read(row, "Type"), "person")
        self.assertEqual(N.read(row, "Name"), "Jane")
        self.assertTrue(N.read(row, "Last Checked"))
        post = nt.row("posts")
        self.assertEqual(N.read(post, "Post URL"), "https://www.linkedin.com/posts/p-activity-1-q")
        self.assertEqual(N.read(post, "Is Repost"), "No")
        self.assertEqual(N.read(post, "Reactions"), 10)
        self.assertEqual(uni.listed, [("ACo-jane", False, fetch.MAX_POSTS_PER_ACCOUNT)])

    def test_company_uses_company_flag_and_existing_provider_id_not_resolved(self):
        nt = FakeNotion(watchlist=[wl("https://www.linkedin.com/company/acme", provider="123")])
        uni = FakeUnipile({"123": [item(1)]})
        run_fetch(uni, nt)
        self.assertEqual(uni.resolved, [])
        self.assertEqual(uni.listed[0][:2], ("123", True))

    def test_paused_accounts_skipped(self):
        nt = FakeNotion(watchlist=[wl("https://www.linkedin.com/in/a", status="Paused"),
                                   wl("https://www.linkedin.com/in/b", status="")])
        uni = FakeUnipile()
        run_fetch(uni, nt)
        self.assertEqual([r[1] for r in uni.resolved], ["b"])

    def test_seen_recent_post_only_counters_refreshed(self):
        existing = {"Post URL": N.url("https://www.linkedin.com/posts/p-activity-1-q"), "Title": N.title("old title"),
                    "Reactions": N.num(1), "Comments": N.num(0), "Reposts": N.num(0),
                    "Posted At": N.date(iso(days=2)), "Collected At": N.date(iso(days=2))}
        nt = FakeNotion(watchlist=[wl("https://www.linkedin.com/in/jane", provider="P")], posts=[existing])
        uni = FakeUnipile({"P": [item(1, reaction_counter=55, comment_counter=9, repost_counter=3)]})
        code, n, _ = run_fetch(uni, nt)
        self.assertEqual((code, n), (0, 0))
        self.assertEqual(len(nt.pages["posts"]), 1)
        post = nt.row("posts")
        self.assertEqual((N.read(post, "Reactions"), N.read(post, "Comments"), N.read(post, "Reposts")), (55, 9, 3))
        self.assertTrue(N.read(post, "Counters Updated"))
        self.assertEqual(N.read(post, "Title"), "old title")  # nothing else touched

    def test_seen_old_post_left_alone(self):
        existing = {"Post URL": N.url("https://www.linkedin.com/posts/p-activity-1-q"), "Reactions": N.num(1),
                    "Posted At": N.date(iso(days=30)), "Collected At": N.date(iso(days=29))}
        nt = FakeNotion(watchlist=[wl("https://www.linkedin.com/in/jane", provider="P")], posts=[existing])
        uni = FakeUnipile({"P": [item(1, parsed_datetime=iso(days=30), reaction_counter=99)]})
        run_fetch(uni, nt)
        self.assertEqual(N.read(nt.row("posts"), "Reactions"), 1)
        self.assertEqual(N.read(nt.row("posts"), "Counters Updated"), "")

    def test_post_seen_under_different_query_string_is_not_duplicated(self):
        existing = {"Post URL": N.url("https://www.linkedin.com/posts/p-activity-1-q/?trk=x"),
                    "Posted At": N.date(iso(days=30))}
        nt = FakeNotion(watchlist=[wl("https://www.linkedin.com/in/jane", provider="P")], posts=[existing])
        code, n, _ = run_fetch(FakeUnipile({"P": [item(1, parsed_datetime=iso(days=30))]}), nt)
        self.assertEqual(n, 0)
        self.assertEqual(len(nt.pages["posts"]), 1)

    def test_normal_run_returns_zero_even_with_new_posts(self):
        nt = FakeNotion(watchlist=[wl("https://www.linkedin.com/in/jane", provider="P")])
        code, n, _ = run_fetch(FakeUnipile({"P": [item(i) for i in range(3)]}), nt)
        self.assertEqual((code, n), (0, 3))
        self.assertEqual(nt.log_calls[0][1], "ok")

    def test_main_prints_count_and_returns_zero(self):
        nt = FakeNotion(watchlist=[wl("https://www.linkedin.com/in/jane", provider="P")])
        with mock.patch.object(fetch, "Unipile", return_value=FakeUnipile({"P": [item(1)]})), \
             mock.patch.object(fetch, "Notion", return_value=nt), \
             mock.patch.object(fetch, "write_results_md"), mock.patch("builtins.print") as pr:
            self.assertEqual(fetch.main(), 0)
        pr.assert_called_with("NEW_POSTS=1")

    def test_initial_auth_failure_exit_2_and_writes_nothing(self):
        nt = FakeNotion(watchlist=[wl("https://www.linkedin.com/in/jane")])
        uni = FakeUnipile(auth_error=AuthError(401, "bad"))
        with mock.patch.object(fetch, "Unipile", return_value=uni), mock.patch.object(fetch, "Notion", return_value=nt), \
             mock.patch.object(fetch, "write_results_md") as md, mock.patch("builtins.print") as pr:
            self.assertEqual(fetch.main(), 2)
        pr.assert_called_with("NEW_POSTS=0")
        self.assertEqual(nt.writes, 0)
        self.assertEqual(nt.pages["posts"], [])
        md.assert_not_called()
        self.assertEqual(uni.listed, [])

    def test_bad_account_does_not_stop_run(self):
        nt = FakeNotion(watchlist=[
            wl("https://www.linkedin.com/feed/", name="junk"),
            wl("https://www.linkedin.com/in/boom", name="boom", provider="B"),
            wl("https://www.linkedin.com/in/ok", name="ok", provider="O"),
        ])
        uni = FakeUnipile({"O": [item(1)]}, fail={"B": UnipileError(500, "kaput")})
        code, n, md = run_fetch(uni, nt)
        self.assertEqual((code, n), (0, 1))
        self.assertIn("not a LinkedIn", N.read(nt.row("watchlist", 0), "Last Error"))
        self.assertIn("kaput", N.read(nt.row("watchlist", 1), "Last Error"))
        self.assertEqual(N.read(nt.row("watchlist", 2), "Last Error"), "")
        self.assertTrue(N.read(nt.row("watchlist", 2), "Last Checked"))
        self.assertEqual(nt.log_calls[0][1], "partial")

    def test_success_clears_previous_error(self):
        nt = FakeNotion(watchlist=[wl("https://www.linkedin.com/in/ok", provider="O", **{"Last Error": N.text("old")})])
        run_fetch(FakeUnipile({"O": []}), nt)
        self.assertEqual(N.read(nt.row("watchlist"), "Last Error"), "")

    def test_mid_run_auth_error_aborts_with_2(self):
        nt = FakeNotion(watchlist=[
            wl("https://www.linkedin.com/in/a", provider="A"), wl("https://www.linkedin.com/in/b", provider="B"),
            wl("https://www.linkedin.com/in/c", provider="C")])
        uni = FakeUnipile({"A": [item(1)]}, fail={"B": AuthError(403, "forbidden")})
        code, n, _ = run_fetch(uni, nt)
        self.assertEqual((code, n), (2, 1))
        self.assertEqual([c[0] for c in uni.listed], ["A", "B"])  # C never fetched
        self.assertEqual(nt.log_calls[0][1], "auth_failed")

    def test_delay_between_accounts_only(self):
        nt = FakeNotion(watchlist=[wl(f"https://www.linkedin.com/in/{s}", provider=s) for s in "abc"])
        delays = []
        with mock.patch.object(fetch, "write_results_md"):
            fetch.run(FakeUnipile(), nt, sleep=delays.append, now=NOW)
        self.assertEqual(len(delays), 2)
        self.assertTrue(all(fetch.DELAY_MIN_S <= d <= fetch.DELAY_MAX_S for d in delays))


class AddToWatchlistTests(unittest.TestCase):
    def test_add_skips_duplicates_and_invalid(self):
        nt = FakeNotion(watchlist=[wl("https://www.linkedin.com/in/jane")])
        lines = ["# comment", "", "https://www.linkedin.com/in/jane/", "https://www.linkedin.com/in/bob, Bob B.",
                 "https://www.linkedin.com/company/acme?x=1", "https://www.linkedin.com/company/acme",
                 "https://www.linkedin.com/posts/x-activity-1", "https://example.com/in/z"]
        added, dupes, invalid = add_wl.add(nt, lines)
        self.assertEqual((added, dupes, invalid), (2, 1, 2))
        rows = nt.pages["watchlist"]
        self.assertEqual(len(rows), 3)
        self.assertEqual((N.read(rows[1], "Name"), N.read(rows[1], "Type"), N.read(rows[1], "Status")),
                         ("Bob B.", "person", "Active"))
        self.assertEqual(N.read(rows[2], "LinkedIn URL"), "https://www.linkedin.com/company/acme")
        self.assertEqual(N.read(rows[2], "Type"), "company")


class MonitorTests(unittest.TestCase):
    def test_parse_int(self):
        self.assertEqual(monitor.parse_int("NEW_POSTS", "x\nNEW_POSTS=3"), 3)
        self.assertEqual(monitor.parse_int("NEW_POSTS", "nothing"), 0)

    def test_ok(self):
        with mock.patch.object(monitor, "run", return_value=(0, "NEW_POSTS=4")) as m:
            self.assertEqual(monitor.main(), 0)
        m.assert_called_once_with("fetch_watchlist_posts.py")

    def test_failures_return_one(self):
        for code in (2, -1, 1):
            with mock.patch.object(monitor, "run", return_value=(code, "NEW_POSTS=3")):
                self.assertEqual(monitor.main(), 1, code)


if __name__ == "__main__":
    unittest.main()
