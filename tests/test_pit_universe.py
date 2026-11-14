"""Offline tests for BIST historical membership, anti-leakage and coverage gates."""
from __future__ import annotations
import sys
import unittest
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from io import StringIO
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
from pit_universe import Membership, read_membership

CSV = b"""year,snapshot_date,ticker,name,index,delisted_in_year
2017,2017-01-01,AAA,Old Company,,False
2017,2017-01-01,BBB,Survivor,XU100,False
2017,2017-04-01,BBB,Survivor,XU100,False
2017,2017-04-01,CCC,IPO, ,False
2017,2017-07-01,BBB,Survivor,XU100,False
2017,2017-07-01,CCC,IPO,,False
"""


class MembershipTests(unittest.TestCase):
    def setUp(self):
        self.m = Membership(read_membership(CSV))

    def test_membership_does_not_know_future_ipos(self):
        self.assertIsNone(self.m.at("2016-12-31"))
        self.assertEqual(self.m.at("2017-02-10"), {"AAA", "BBB"})
        self.assertEqual(self.m.at("2017-04-02"), {"BBB", "CCC"})

    def test_delisted_symbols_remain_in_historical_union(self):
        self.assertEqual(self.m.in_range("2017-01-01", "2017-05-01"),
                         {"AAA", "BBB", "CCC"})
        self.assertEqual(self.m.in_range("2017-05-01", "2017-07-20"),
                         {"BBB", "CCC"})

    def test_bad_duplicate_name_and_invalid_symbol_rejected(self):
        bad = CSV + b"2017,2017-01-01,AAA,Other Company,,False\n"
        with self.assertRaisesRegex(ValueError, "CONFLICTING_NAMES"):
            read_membership(bad)
        with self.assertRaisesRegex(ValueError, "INVALID_SYMBOL"):
            read_membership(b"snapshot_date,ticker\n2017-01-01,NOT A SYMBOL\n")

    def test_price_catalog_does_not_get_verified_status(self):
        a = self.m.audit("2017-01-01", "2017-06-01", {"BBB"})
        self.assertEqual(a["status"], "NOT_VERIFIED")
        self.assertFalse(a["survivorship_bias_resolved"])
        self.assertIn("AAA", a["symbols_without_price_series_examples"])
        self.assertEqual(a["pricing_audit_method"], "SYMBOL_CATALOG_ONLY")

    def test_two_bars_do_not_count_as_feature_history(self):
        idx = pd.bdate_range("2016-01-01", periods=390)
        good = pd.DataFrame({"close": np.full(len(idx), 10.0)}, index=idx)
        short = good.tail(2)
        a = self.m.audit("2017-01-01", "2017-03-01",
                         {"AAA": short, "BBB": good})
        first = a["monthly_coverage"][0]
        self.assertEqual(first["members"], 2)
        self.assertEqual(first["usable_asof_price_history"], 1)
        self.assertLess(first["coverage_ratio"], 0.95)

    def test_catalog_only_never_claims_missing_prices_without_query(self):
        from run_pit_audit import run
        with TemporaryDirectory() as d:
            csv = Path(d) / "members.csv"
            csv.write_bytes(CSV)
            output = Path(d) / "audit.json"
            result = run(SimpleNamespace(
                membership_csv=str(csv), membership_only=True,
                start="2017-01-01", end="2017-07-01", retest=False,
                output=str(output)))
            self.assertEqual(result["audit"]["historical_tickers"], 3)
            self.assertIsNone(result["audit"]["symbols_without_price_series"])
            self.assertEqual(result["audit"]["price_coverage_status"], "NOT_QUERIED")
            self.assertFalse(result["validated_survivorship_free_alpha"])
            self.assertTrue(output.exists())

    def test_no_future_price_is_used_at_past_rebalance(self):
        idx = pd.bdate_range("2017-04-01", periods=265)
        future = pd.DataFrame({"close": np.full(len(idx), 15.)}, index=idx)
        a = self.m.audit("2017-01-01", "2017-03-01",
                         {"AAA": future, "BBB": future})
        self.assertEqual(a["monthly_coverage"][0]["usable_asof_price_history"], 0)


if __name__ == "__main__":
    unittest.main()
