import json, subprocess, sys, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import monitor
from lib.logic import select_eligible
from lib.models import Engager
from lib.unipile import parse_comment, parse_post, parse_reaction
from lib.util import canonical_url, extract_json, split_name

NOW = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
POST = {"post_url": "https://www.linkedin.com/posts/x-share-123-abc", "author": "Ann"}


class Core(unittest.TestCase):
    def test_canonical_keeps_permalink_type(self):
        self.assertEqual(canonical_url("https://www.linkedin.com/posts/a-share-99-xy/?utm=1#f"),
                         "https://www.linkedin.com/posts/a-share-99-xy")
        self.assertEqual(canonical_url("https://www.linkedin.com/feed/update/urn:li:ugcPost:5/"),
                         "https://www.linkedin.com/feed/update/urn:li:ugcPost:5")

    def test_eligibility(self):
        d = lambda **k: NOW - timedelta(**k)
        posts = [
            {"id": "new", "collected_at": d(days=1), "last_scraped": None},
            {"id": "fresh", "collected_at": d(days=2), "last_scraped": d(hours=3)},
            {"id": "due", "collected_at": d(days=3), "last_scraped": d(hours=30)},
            {"id": "old", "collected_at": d(days=9), "last_scraped": None},
        ]
        ids = [p["id"] for p in select_eligible(posts, NOW, 7, 24)]
        self.assertEqual(ids, ["new", "due"])
        self.assertEqual(len(select_eligible(posts, NOW, 7, 24, ignore_filters=True)), 4)
        self.assertEqual(len(select_eligible(posts, NOW, 7, 24, cap=1)), 1)

    def test_parse_post_preserves_url(self):
        p = parse_post({"social_id": "urn:li:activity:1", "share_url": "https://www.linkedin.com/posts/a-share-1-z?x=1",
                        "text": "hello  world", "author": {"name": "Bob", "public_identifier": "bob"}}, "kw")
        self.assertEqual(p.post_url, "https://www.linkedin.com/posts/a-share-1-z")
        self.assertEqual(p.post_author_profile_url, "https://www.linkedin.com/in/bob")
        fb = parse_post({"social_id": "urn:li:ugcPost:7"}, "kw")
        self.assertEqual(fb.post_url, "https://www.linkedin.com/feed/update/urn:li:ugcPost:7")

    def test_engager_parsing_and_keys(self):
        r = parse_reaction({"value": "INSIGHTFUL", "author": {"name": "Cy", "headline": "Partner", "public_identifier": "cy"}}, POST)
        self.assertEqual((r.reaction_type, r.linkedin_url), ("INSIGHTFUL", "https://www.linkedin.com/in/cy"))
        c1 = parse_comment({"text": "Great  point", "author": "Di", "author_details": {"id": "ACo1", "headline": "CFO"}}, POST)
        c2 = parse_comment({"text": "Great point", "author": "Di", "author_details": {"id": "ACo1"}}, POST)
        self.assertEqual(c1.key, c2.key)  # whitespace-normalised dedup
        self.assertNotEqual(r.key, c1.key)
        self.assertIsNone(parse_comment({"text": "x"}, POST))

    def test_helpers(self):
        self.assertEqual(split_name("Anne-Marie de La Tour"), ("Anne-Marie", "de La Tour"))
        self.assertEqual(extract_json('ok ```json\n{"a": [1]}\n``` bye'), {"a": [1]})

    def test_monitor_parses_new_posts_despite_nonzero_exit(self):
        seq = iter([(1, "log\nNEW_POSTS=3\n"), (0, ""), (0, ""), (0, "")])
        with mock.patch.object(monitor, "run", side_effect=lambda *a: next(seq)) as m:
            self.assertEqual(monitor.parse_int("NEW_POSTS", "x\nNEW_POSTS=3"), 3)
            self.assertEqual(monitor.main(), 0)
            self.assertEqual(m.call_count, 4)  # engagement still ran

    def test_monitor_stops_on_auth_failure(self):
        with mock.patch.object(monitor, "run", return_value=(2, "NEW_POSTS=0")) as m:
            self.assertEqual(monitor.main(), 1)
            self.assertEqual(m.call_count, 1)

    def test_search_script_returns_zero_and_prints_count(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("s", ROOT / "03-workflows/search_linkedin_posts.py")
        s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)
        posts = [parse_post({"social_id": f"urn:li:activity:{i}", "share_url": f"https://www.linkedin.com/posts/p-activity-{i}-q", "text": "t"}, "kw") for i in (1, 2)]
        uni, notion = mock.Mock(), mock.Mock()
        uni.search_posts.return_value = posts + posts[:1]  # duplicate in batch
        notion.query.return_value = []
        with mock.patch.object(s, "Unipile", return_value=uni), mock.patch.object(s, "Notion", return_value=notion), \
             mock.patch.object(sys, "argv", ["x", "--no-filter"]), mock.patch.object(s, "KEYWORDS", ["kw"]), \
             mock.patch("builtins.print") as pr:
            self.assertEqual(s.main(), 0)
        self.assertEqual(notion.create.call_count, 2)
        pr.assert_called_with("NEW_POSTS=2")


if __name__ == "__main__":
    unittest.main()
