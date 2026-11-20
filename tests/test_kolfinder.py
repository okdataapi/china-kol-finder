"""Runs kolfinder.py against a local mock of the okdata API with canned answers. No network, no real key."""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
os.environ["NO_PROXY"] = "*"

import kolfinder as tool  # noqa: E402
from mock_okdata import TEST_KEY, MockOkdata, error, ok  # noqa: E402

API = json.loads((FIXTURES / "api.json").read_text(encoding="utf-8"))


class FinderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.api = MockOkdata({"/v1/douyin/xingtu/creators/search": self.douyin,
                               "/v1/xiaohongshu/pgy/creators/search": self.xiaohongshu})
        tool.API = self.api.url
        self.tmp = tempfile.TemporaryDirectory()
        self.csv = Path(self.tmp.name) / "out" / "shortlist.csv"

    def tearDown(self) -> None:
        self.api.close()
        self.tmp.cleanup()

    def douyin(self, query: dict) -> tuple:
        pages = API["douyin"]
        if query.get("cursor") == "p2":
            return 200, ok("douyin.xingtu.creators.search", items=pages[1], amount="0.004")
        return 200, ok("douyin.xingtu.creators.search", items=pages[0], next_cursor="p2", amount="0.004")

    def xiaohongshu(self, query: dict) -> tuple:
        return 200, ok("xiaohongshu.pgy.creators.search", items=API["xiaohongshu"][0], amount="0.04")

    def run_tool(self, *argv: str, key: str = TEST_KEY) -> tuple:
        os.environ["OKDATA_API_KEY"] = key
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = tool.main([*argv, "--out", str(self.csv)])
        return code, out.getvalue(), err.getvalue()

    def rows(self) -> list:
        return list(csv.DictReader(io.StringIO(self.csv.read_text(encoding="utf-8-sig"))))

    def test_shortlist_is_sorted_by_cost_per_1k_views_and_deduplicated(self) -> None:
        code, out, log = self.run_tool("--query", " 露营 ", "--followers", "100k-1m", "--pages", "2",
                                       "--cny-per-usd", "7.1")
        self.assertEqual(code, 0, log)
        self.assertIn("spent $0.048", log)  # 2 Douyin pages + 1 Xiaohongshu page
        rows = self.rows()
        self.assertEqual([r["creator_id"] for r in rows], ["dy0003", "dy0001", "dy0004", "dy0002", "xhs0002", "xhs0001"])
        a = rows[1]
        self.assertEqual((a["cheapest_rate"], a["cheapest_rate_cny"], a["cny_per_1k_views"]),
                         ("integrated video", "18800", "31.33"))
        self.assertEqual((a["cheapest_rate_usd"], a["usd_per_1k_views"]), ("2647.89", "4.41"))
        self.assertEqual(a["engagement_rate_30d"], "0.0329")
        self.assertEqual(a["engagements_per_view"], "0.0117")
        self.assertEqual(a["rates"], "integrated video 18800; custom video 21800")
        self.assertEqual(out, (FIXTURES / "expected-table.txt").read_text(encoding="utf-8"))
        searches = [r["query"] for r in self.api.paid()]
        self.assertEqual({(q["query"], q["followers_min"], q["followers_max"]) for q in searches},
                         {("露营", "100000", "1000000")})
        self.assertEqual({q["search_by"] for q in searches}, {"content", "notes"})

    def test_a_rate_not_taking_orders_is_listed_but_not_the_cheapest(self) -> None:
        self.run_tool("--query", "露营", "--platform", "xiaohongshu")
        f = next(r for r in self.rows() if r["creator_id"] == "xhs0002")
        self.assertEqual((f["cheapest_rate"], f["cheapest_rate_cny"], f["cny_per_1k_views"]), ("video note", "3000", "10.00"))
        self.assertIn("image note 2000 (not taking orders)", f["rates"])

    def test_missing_rates_or_views_sort_last_and_stay_blank(self) -> None:
        self.run_tool("--query", "露营", "--platform", "douyin", "--pages", "2")
        rows = self.rows()
        self.assertEqual([r["creator_id"] for r in rows][-2:], ["dy0004", "dy0002"])  # then by followers
        by_id = {r["creator_id"]: r for r in rows}
        self.assertEqual(by_id["dy0004"]["cheapest_rate_cny"], "")  # no rates quoted
        self.assertEqual((by_id["dy0002"]["cheapest_rate_cny"], by_id["dy0002"]["cny_per_1k_views"]), ("9000", ""))
        self.assertEqual(by_id["dy0002"]["engagements_per_view"], "")  # median views 0

    def test_csv_is_safe_for_spreadsheets_and_names_are_clipped(self) -> None:
        self.run_tool("--query", "露营", "--platform", "douyin", "--pages", "2")
        rows = {r["creator_id"]: r for r in self.rows()}
        self.assertTrue(rows["dy0002"]["name"].startswith("'="))
        self.assertLessEqual(len(rows["dy0004"]["name"]), tool.NAME_LIMIT)
        self.assertTrue(rows["dy0004"]["name"].endswith("…"))
        self.assertTrue(self.csv.read_bytes().startswith(b"\xef\xbb\xbf"))  # BOM, so Excel reads the Chinese

    def test_name_search_uses_name_on_both_platforms(self) -> None:
        self.run_tool("--query", "露营", "--by", "name")
        self.assertEqual({r["query"]["search_by"] for r in self.api.paid()}, {"name"})

    def test_dry_run_needs_no_key_and_calls_nothing_paid(self) -> None:
        code, _, log = self.run_tool("--query", "露营", "--pages", "2", "--dry-run", key="")
        self.assertEqual(code, 0, log)
        self.assertIn("at most $0.088", log)  # 2 x $0.004 + 2 x $0.04
        self.assertEqual(self.api.paid(), [])
        self.assertFalse(self.csv.exists())

    def test_over_budget_calls_nothing(self) -> None:
        code, _, log = self.run_tool("--query", "露营", "--pages", "20")
        self.assertEqual(code, 3)
        self.assertEqual(self.api.paid(), [])

    def test_bad_key_stops_the_run(self) -> None:
        code, out, log = self.run_tool("--query", "露营", key="okd_wrong")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("unauthorized", log)
        self.assertEqual(len(self.api.paid()), 1)

    def test_one_failed_platform_keeps_the_other(self) -> None:
        self.api.routes["/v1/xiaohongshu/pgy/creators/search"] = lambda q: (502, error("upstream_error"))
        code, out, log = self.run_tool("--query", "露营")
        self.assertEqual(code, 4)
        self.assertIn("upstream_error", log)
        self.assertEqual({r["platform"] for r in self.rows()}, {"douyin"})

    def test_missing_key(self) -> None:
        code, _, log = self.run_tool("--query", "露营", key="")
        self.assertEqual(code, 2)
        self.assertIn("OKDATA_API_KEY is not set", log)


class ArgumentTest(unittest.TestCase):
    def test_follower_ranges(self) -> None:
        self.assertEqual(tool.followers("100k-1m"), (100_000, 1_000_000))
        self.assertEqual(tool.followers("1.5m-"), (1_500_000, None))
        self.assertEqual(tool.followers("-50,000"), (None, 50_000))
        for bad in ("100k", "1m-100k", "abc-", "10x-"):
            with self.assertRaises(Exception):
                tool.followers(bad)

    def test_defaults(self) -> None:
        args = tool.parse_args(["--query", "露营", "--query", "露营"])
        self.assertEqual(args.platform, ["douyin", "xiaohongshu"])
        self.assertEqual(args.query, ["露营"])
        self.assertIsNone(args.cny_per_usd)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tool.parse_args(["--query", "露营", "--cny-per-usd", "0"])

    def test_default_file_name(self) -> None:
        self.assertTrue(str(tool.default_out(["露营 装备", "camping/gear"])).startswith("shortlists/露营-装备-camping-gear-"))

    def test_table_fits_chinese_names(self) -> None:
        self.assertEqual(tool.width(tool.fit("露营达人名字很长很长很长", 18)), 18)
        self.assertEqual(tool.fit("abc", 5), "abc  ")


if __name__ == "__main__":
    unittest.main()
