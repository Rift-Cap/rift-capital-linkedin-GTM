import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import monitor
from lib import attio as A
from lib import notion as N
from lib.lemlist import Lemlist, LemlistError
from lib.unipile import AuthError, UnipileError, parse_comment, parse_post, parse_reaction
from lib.util import canonical_url, parse_account_url


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "03-workflows" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch = load("fetch_watchlist_posts")
add_wl = load("add_to_watchlist")
eng = load("fetch_engagers")
push = load("push_to_lemlist")
NOW = datetime.now(timezone.utc)


def iso(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


class FakeNotion:
    """In-memory Notion: stores pages in the API's read format so N.read works."""

    def __init__(self, watchlist=(), posts=(), engagers=()):
        self.pages = {"watchlist": [], "posts": [], "engagers": [], "runs": []}
        self.n = 0
        for props in watchlist:
            self.create("watchlist", props)
        for props in posts:
            self.create("posts", props)
        for props in engagers:
            self.create("engagers", props)
        self.dbs = {"engagers": "x"}
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
    def __init__(self, posts=None, auth_error=None, fail=None, reactions=None, comments=None, fail_posts=None):
        self.reactions, self.comments, self.fail_posts = reactions or {}, comments or {}, fail_posts or {}
        self.react_calls, self.comment_calls = [], []
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

    def list_reactions(self, social_id, limit):
        self.react_calls.append((social_id, limit))
        if social_id in self.fail_posts:
            raise self.fail_posts[social_id]
        return [parse_reaction(i) for i in self.reactions.get(social_id, [])]

    def list_comments(self, social_id, limit):
        self.comment_calls.append((social_id, limit))
        return [parse_comment(i) for i in self.comments.get(social_id, [])]

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


class EngagerTests(unittest.TestCase):
    @staticmethod
    def post(i, days=1, social=True, url=None):
        d = {"Post URL": N.url(url or f"https://www.linkedin.com/posts/p-activity-{i}-q"),
             "Social ID": N.text(f"urn:li:activity:{i}" if social else ""), "Account": N.text("Acme"),
             "Posted At": N.date(iso(days=days)), "Collected At": N.date(iso(days=days))}
        return d

    @staticmethod
    def react(name, value="LIKE", pid=None):
        return {"value": value, "author": {"name": name, "headline": "Partner", "public_identifier": pid or name.lower()}}

    @staticmethod
    def comment(name, text, aid="ACo1"):
        return {"text": text, "author": name, "author_details": {"id": aid, "headline": "CFO", "public_identifier": aid}}

    def run_eng(self, uni, nt):
        with mock.patch.object(eng, "write_results_md") as md:
            code, n = eng.run(uni, nt, sleep=lambda s: None, now=NOW)
        return code, n, md

    def test_parsers(self):
        r = parse_reaction(self.react("Cy", "INSIGHTFUL"))
        self.assertEqual((r["name"], r["reaction_type"], r["source"], r["profile_url"]),
                         ("Cy", "INSIGHTFUL", "Reaction", "https://www.linkedin.com/in/cy"))
        c = parse_comment(self.comment("Di", "Great   point"))
        self.assertEqual((c["comment_text"], c["source"], c["headline"]), ("Great point", "Comment", "CFO"))
        self.assertIsNone(parse_comment({"text": "x"}))
        self.assertIsNone(parse_reaction({"value": "LIKE"}))

    def test_collects_reactions_and_comments_with_caps(self):
        nt = FakeNotion(posts=[self.post(1)])
        uni = FakeUnipile(reactions={"urn:li:activity:1": [self.react("Ann"), self.react("Bob", "PRAISE")]},
                          comments={"urn:li:activity:1": [self.comment("Cy", "Nice", "ACo9")]})
        code, n, _ = self.run_eng(uni, nt)
        self.assertEqual((code, n), (0, 3))
        rows = nt.pages["engagers"]
        self.assertEqual({N.read(r, "Source") for r in rows}, {"Reaction", "Comment"})
        bob = next(r for r in rows if N.read(r, "Name") == "Bob")
        self.assertEqual((N.read(bob, "Reaction Type"), N.read(bob, "Post URL"), N.read(bob, "Account")),
                         ("PRAISE", "https://www.linkedin.com/posts/p-activity-1-q", "Acme"))
        cy = next(r for r in rows if N.read(r, "Name") == "Cy")
        self.assertEqual((N.read(cy, "Comment Text"), N.read(cy, "Headline")), ("Nice", "CFO"))
        self.assertEqual(uni.react_calls, [("urn:li:activity:1", eng.MAX_REACTIONS_PER_POST)])
        self.assertEqual(uni.comment_calls, [("urn:li:activity:1", eng.MAX_COMMENTS_PER_POST)])

    def test_second_run_does_not_duplicate(self):
        nt = FakeNotion(posts=[self.post(1)])
        uni = FakeUnipile(reactions={"urn:li:activity:1": [self.react("Ann")]},
                          comments={"urn:li:activity:1": [self.comment("Cy", "Nice")]})
        self.run_eng(uni, nt)
        code, n, _ = self.run_eng(uni, nt)
        self.assertEqual((code, n), (0, 0))
        self.assertEqual(len(nt.pages["engagers"]), 2)

    def test_new_comment_by_same_person_is_kept_same_comment_is_not(self):
        nt = FakeNotion(posts=[self.post(1)])
        uni = FakeUnipile(comments={"urn:li:activity:1": [self.comment("Cy", "Nice"), self.comment("Cy", "Nice"),
                                                           self.comment("Cy", "A second, different comment")]})
        _, n, _ = self.run_eng(uni, nt)
        self.assertEqual(n, 2)

    def test_only_recent_posts_with_social_id(self):
        nt = FakeNotion(posts=[self.post(1, days=2), self.post(2, days=20), self.post(3, social=False)])
        uni = FakeUnipile()
        self.run_eng(uni, nt)
        self.assertEqual([c[0] for c in uni.react_calls], ["urn:li:activity:1"])

    def test_auth_failure_exit_2_writes_nothing(self):
        nt = FakeNotion(posts=[self.post(1)])
        uni = FakeUnipile(auth_error=AuthError(401, "bad"))
        with mock.patch.object(eng, "Unipile", return_value=uni), mock.patch.object(eng, "Notion", return_value=nt), \
             mock.patch.object(eng, "write_results_md") as md, mock.patch("builtins.print") as pr:
            self.assertEqual(eng.main(), 2)
        pr.assert_called_with("NEW_ENGAGERS=0")
        self.assertEqual((nt.writes, nt.pages["engagers"]), (0, []))
        md.assert_not_called()

    def test_bad_post_does_not_stop_run_and_mid_run_auth_aborts(self):
        nt = FakeNotion(posts=[self.post(1), self.post(2), self.post(3), self.post(4)])
        uni = FakeUnipile(reactions={"urn:li:activity:2": [self.react("Ann")], "urn:li:activity:3": [self.react("Bob")]},
                          fail_posts={"urn:li:activity:1": UnipileError(500, "kaput"),
                                      "urn:li:activity:3": AuthError(403, "no")})
        code, n, _ = self.run_eng(uni, nt)
        self.assertEqual((code, n), (2, 1))  # post 1 failed (continued), post 2 ok, post 3 auth -> abort, 4 untouched
        self.assertNotIn("urn:li:activity:4", [c[0] for c in uni.react_calls])
        self.assertEqual(nt.log_calls[0][1], "auth_failed")

    def test_partial_status_and_exit_zero(self):
        nt = FakeNotion(posts=[self.post(1), self.post(2)])
        uni = FakeUnipile(reactions={"urn:li:activity:2": [self.react("Ann")]},
                          fail_posts={"urn:li:activity:1": UnipileError(500, "kaput")})
        code, n, _ = self.run_eng(uni, nt)
        self.assertEqual((code, n), (0, 1))
        self.assertEqual(nt.log_calls[0][1], "partial")

    def test_time_budget_stops_cleanly_and_next_run_resumes(self):
        nt = FakeNotion(posts=[self.post(1), self.post(2), self.post(3)])
        reacts = {f"urn:li:activity:{i}": [self.react(f"P{i}-{j}") for j in range(3)] for i in (1, 2, 3)}
        uni = FakeUnipile(reactions=reacts)
        ticks = iter(range(0, 1000))  # each clock() call advances 1 "second"
        with mock.patch.object(eng, "write_results_md"):
            # budget 4: start(0) -> post1 check(1), 3 row checks(2,3,4 -> stops at the 3rd)
            code, n = eng.run(uni, nt, sleep=lambda s: None, now=NOW, clock=lambda: next(ticks), budget_s=4)
        self.assertEqual(code, 0)
        self.assertGreater(n, 0)
        self.assertLess(len(nt.pages["engagers"]), 9)
        self.assertEqual(nt.log_calls[-1][1], "partial")
        self.assertIn("time budget", nt.log_calls[-1][2])
        # next run with plenty of time finishes everything, no duplicates
        code, _, _ = self.run_eng(uni, nt)
        self.assertEqual(code, 0)
        self.assertEqual(len(nt.pages["engagers"]), 9)
        self.assertEqual(nt.log_calls[-1][1], "ok")

    def test_least_covered_posts_go_first(self):
        covered = {"Name": N.title("old"), "Key": N.text("k1"), "Post URL": N.url("https://www.linkedin.com/posts/p-activity-1-q")}
        nt = FakeNotion(posts=[self.post(1), self.post(2)], engagers=[covered])
        uni = FakeUnipile()
        self.run_eng(uni, nt)
        self.assertEqual([c[0] for c in uni.react_calls], ["urn:li:activity:2", "urn:li:activity:1"])

    def test_main_skips_when_db_not_configured(self):
        nt = FakeNotion()
        nt.dbs = {"engagers": ""}
        with mock.patch.object(eng, "Notion", return_value=nt), mock.patch.object(eng, "Unipile") as u, \
             mock.patch("builtins.print") as pr:
            self.assertEqual(eng.main(), 0)
        pr.assert_called_with("NEW_ENGAGERS=0")
        u.assert_not_called()


class AttioDedupTests(unittest.TestCase):
    RECORDS = [
        {"values": {"name": [{"full_name": "Ann Lee"}], "linkedin": [{"value": "https://www.linkedin.com/in/ann-lee-1/"}]}},
        {"values": {"name": [{"full_name": "José Martín"}]}},                       # no LinkedIn -> name match allowed
        {"values": {"name": [{"full_name": "Bob Stone"}], "linkedin": [{"value": "https://fr.linkedin.com/in/bob-s"}]}},
        {"values": {"name": [{"full_name": "Madonna"}]}},                           # single word never matches
    ]

    def test_slug_and_name_keys(self):
        self.assertEqual(A.linkedin_slug("https://www.linkedin.com/in/Ann-Lee-1/?x=1"), "ann-lee-1")
        self.assertEqual(A.linkedin_slug("https://www.linkedin.com/company/acme"), "")
        self.assertEqual(A.name_key("Jose  MARTIN"), A.name_key("Martín, José"))
        self.assertEqual(A.name_key("Madonna"), "")

    def test_index_matching(self):
        ix = A.build_index(self.RECORDS)
        self.assertTrue(ix.contains("Whoever", "https://www.linkedin.com/in/ann-lee-1"))   # URL match
        self.assertTrue(ix.contains("Jose Martin", ""))                                     # name match, Attio has no URL
        self.assertFalse(ix.contains("Bob Stone", "https://www.linkedin.com/in/other-bob"))  # has other URL -> different person
        self.assertFalse(ix.contains("Madonna", ""))

    def test_run_skips_people_in_attio(self):
        nt = FakeNotion(posts=[EngagerTests.post(1)])
        uni = FakeUnipile(reactions={"urn:li:activity:1": [
            EngagerTests.react("Ann Lee", pid="ann-lee-1"), EngagerTests.react("New Person", pid="new-person")]},
            comments={"urn:li:activity:1": [EngagerTests.comment("Jose Martin", "hi", "ACo7")]})
        with mock.patch.object(eng, "write_results_md"):
            code, n = eng.run(uni, nt, sleep=lambda s: None, now=NOW, attio=A.build_index(self.RECORDS))
        self.assertEqual((code, n), (0, 1))
        self.assertEqual([N.read(r, "Name") for r in nt.pages["engagers"]], ["New Person"])
        self.assertIn("2 already in Attio", nt.log_calls[-1][2])

    def test_load_index_paginates_and_reports_errors(self):
        class R:
            def __init__(self, code, data): self.status_code, self._d, self.text = code, data, ""
            def json(self): return {"data": self._d}
        page = [{"values": {"linkedin": [{"value": f"https://www.linkedin.com/in/u{i}"}]}} for i in range(A.PAGE)]
        with mock.patch.object(A, "http", side_effect=[R(200, page), R(200, [{"values": {"linkedin": [{"value": "https://www.linkedin.com/in/last"}]}}])]) as m:
            ix = A.load_index("k")
        self.assertEqual(m.call_count, 2)
        self.assertIn("last", ix.slugs)
        with mock.patch.object(A, "http", return_value=R(401, [])):
            with self.assertRaises(A.AttioError):
                A.load_index("k")
        with self.assertRaises(A.AttioError):
            with mock.patch.dict("os.environ", {}, clear=True):
                A.load_index()


class FakeLemlist:
    def __init__(self, fail_for=(), company_for=()):
        self.leads, self.campaigns, self.fail_for, self.company_for = [], [], set(fail_for), set(company_for)

    def get_or_create_campaign(self, name):
        self.campaigns.append(name)
        return "cmp1", True

    def add_lead(self, cid, lead):
        if lead["lastName"] in self.fail_for:
            raise LemlistError("boom")
        if lead["lastName"] in self.company_for:
            return "skipped"
        self.leads.append((cid, lead))
        return "added"


class LemlistPushTests(unittest.TestCase):
    @staticmethod
    def eng_row(name, url, post="1", lemlist=None, headline="CFO"):
        d = {"Name": N.title(name), "Profile URL": N.url(url), "Headline": N.text(headline),
             "Post URL": N.url(f"https://x/{post}"), "Source": N.select("Reaction"), "Key": N.text(f"{name}{post}")}
        if lemlist:
            d["Lemlist"] = N.select(lemlist)
        return d

    def go(self, rows, lem=None, records=()):
        nt = FakeNotion(engagers=rows)
        lem = lem or FakeLemlist()
        code, n = push.run(nt, lem, A.build_index(list(records)), campaign_name="C")
        return nt, lem, code, n

    def test_pushes_once_per_person_and_marks_rows(self):
        rows = [self.eng_row("Ann Lee", "https://www.linkedin.com/in/ann", "1"),
                self.eng_row("Ann Lee", "https://www.linkedin.com/in/ann/", "2"),
                self.eng_row("Bob Stone", "https://www.linkedin.com/in/bob")]
        nt, lem, code, n = self.go(rows)
        self.assertEqual((code, n, len(lem.leads), lem.campaigns), (0, 2, 2, ["C"]))
        self.assertEqual({N.read(r, "Lemlist") for r in nt.pages["engagers"]}, {"Pushed"})
        lead = lem.leads[0][1]
        self.assertEqual((lead["firstName"], lead["lastName"], lead["linkedinUrl"]), ("Ann", "Lee", "https://www.linkedin.com/in/ann"))

    def test_skips_attio_already_pushed_and_no_url(self):
        recs = [{"values": {"name": [{"full_name": "Cy Dee"}], "linkedin": [{"value": "https://www.linkedin.com/in/cy"}]}}]
        rows = [self.eng_row("Cy Dee", "https://www.linkedin.com/in/cy"),
                self.eng_row("Old One", "https://www.linkedin.com/in/old", lemlist="Pushed"),
                self.eng_row("No Url", "")]
        nt, lem, code, n = self.go(rows, records=recs)
        self.assertEqual((code, n, lem.leads, lem.campaigns), (0, 0, [], []))   # campaign not even created
        self.assertEqual([N.read(r, "Lemlist") for r in nt.pages["engagers"]], ["In Attio", "Pushed", "No URL"])

    def test_error_leaves_row_empty_for_retry(self):
        rows = [self.eng_row("Ann Lee", "https://www.linkedin.com/in/ann"), self.eng_row("Bob Stone", "https://www.linkedin.com/in/bob")]
        nt, lem, code, n = self.go(rows, lem=FakeLemlist(fail_for=["Lee"]))
        self.assertEqual((code, n), (1, 1))
        self.assertEqual([N.read(r, "Lemlist") for r in nt.pages["engagers"]], ["", "Pushed"])

    def test_company_pages_are_skipped_not_errors(self):
        rows = [self.eng_row("Acme Inc", "https://www.linkedin.com/company/acme"),
                self.eng_row("Odd Page", "https://www.linkedin.com/in/odd"),
                self.eng_row("Bob Stone", "https://www.linkedin.com/in/bob")]
        nt, lem, code, n = self.go(rows, lem=FakeLemlist(company_for=["Page"]))
        self.assertEqual((code, n), (0, 1))           # exit 0: nothing to retry
        self.assertEqual([N.read(r, "Lemlist") for r in nt.pages["engagers"]], ["Company", "Company", "Pushed"])
        self.assertIn("1 company pages skipped", nt.log_calls[-1][2])

    def test_time_budget_stops_cleanly(self):
        rows = [self.eng_row("Ann Lee", "https://www.linkedin.com/in/ann")]
        nt = FakeNotion(engagers=rows)
        code, n = push.run(nt, FakeLemlist(), A.build_index([]), clock=lambda: 10, budget_s=-1)
        self.assertEqual((code, n), (0, 0))
        self.assertIn("time budget", nt.log_calls[-1][2])

    def test_main_skips_without_keys_and_refuses_without_attio(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch("builtins.print") as pr:
            self.assertEqual(push.main(), 0)
        pr.assert_called_with("PUSHED=0")
        nt = FakeNotion()
        with mock.patch.dict("os.environ", {"LEMLIST_API_KEY": "k"}), mock.patch.object(push, "Notion", return_value=nt), \
             mock.patch.object(push.A, "load_index", side_effect=A.AttioError("down")), mock.patch("builtins.print"):
            self.assertEqual(push.main(), 1)

    def test_client_add_lead_outcomes(self):
        class R:
            def __init__(self, c, t=""): self.status_code, self.text = c, t
        lem = Lemlist("k", sleep=lambda s: None)
        with mock.patch("lib.lemlist.http", return_value=R(200)):
            self.assertEqual(lem.add_lead("c", {}), "added")
        with mock.patch("lib.lemlist.http", return_value=R(400, "Lead already in campaign")):
            self.assertEqual(lem.add_lead("c", {}), "exists")
        with mock.patch("lib.lemlist.http", return_value=R(400, "LinkedIn URL is not a personal profile")):
            self.assertEqual(lem.add_lead("c", {}), "skipped")
        with mock.patch("lib.lemlist.http", return_value=R(500, "x")):
            with self.assertRaises(LemlistError):
                lem.add_lead("c", {})
        with mock.patch("lib.lemlist.http", return_value=R(401)):
            with self.assertRaises(LemlistError):
                lem.add_lead("c", {})


class MonitorTests(unittest.TestCase):
    def test_parse_int(self):
        self.assertEqual(monitor.parse_int("NEW_POSTS", "x\nNEW_POSTS=3"), 3)
        self.assertEqual(monitor.parse_int("NEW_POSTS", "nothing"), 0)

    def test_ok_runs_both_scripts(self):
        with mock.patch.object(monitor, "run", return_value=(0, "NEW_POSTS=4")) as m:
            self.assertEqual(monitor.main(), 0)
        self.assertEqual([c.args[0] for c in m.call_args_list], ["fetch_watchlist_posts.py", "fetch_engagers.py", "push_to_lemlist.py"])

    def test_auth_failure_stops_pipeline(self):
        with mock.patch.object(monitor, "run", return_value=(2, "NEW_POSTS=0")) as m:
            self.assertEqual(monitor.main(), 1)
        self.assertEqual(m.call_count, 1)

    def test_other_failures_return_one_after_running_everything(self):
        for code in (-1, 1):
            with mock.patch.object(monitor, "run", return_value=(code, "NEW_POSTS=3")) as m:
                self.assertEqual(monitor.main(), 1, code)
            self.assertEqual(m.call_count, 3)

    def test_engager_failure_reported(self):
        seq = iter([(0, "NEW_POSTS=1"), (1, "boom"), (0, "PUSHED=0")])
        with mock.patch.object(monitor, "run", side_effect=lambda *a: next(seq)):
            self.assertEqual(monitor.main(), 1)


if __name__ == "__main__":
    unittest.main()
