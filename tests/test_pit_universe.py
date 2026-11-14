"""Offline tests for BIST historical membership, anti-leakage and coverage gates."""
from __future__ import annotations
import sys
import unittest
import json
from unittest.mock import patch
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


    def test_raw_financial_cache_derives_asof_dates_without_writing_cache(self):
        from run_pit_audit import load_research_fundamentals
        import config as C
        with TemporaryDirectory() as d:
            path = Path(d) / "fundamentals_hist.csv"
            rows = [
                {"ticker": "AAA", "year": y, "period": p, "net_income": 100.,
                 "equity": 200., "revenue": 300., "op_profit": 20.,
                 "paid_in": 10., "fin_debt": 15.}
                for y, p in [(2022, 12), (2023, 3), (2023, 12)]
            ]
            pd.DataFrame(rows).to_csv(path, index=False)
            before = path.read_bytes()
            pit, meta = load_research_fundamentals(path)
            self.assertEqual(meta["mode"], "DERIVED_FROM_RAW_CACHE")
            self.assertEqual(meta["records"], 3)
            self.assertFalse(meta["filing_publication_timestamps_verified"])
            annual = pit.loc[(pit["year"] == 2022) & (pit["period"] == 12)].iloc[0]
            self.assertEqual(annual["period_end"], pd.Timestamp("2022-12-31"))
            self.assertEqual(annual["avail_date"],
                             pd.Timestamp("2022-12-31") +
                             pd.Timedelta(days=C.FUND_LAG_ANNUAL_DAYS))
            self.assertIn("net_income_ttm", pit.columns)
            self.assertEqual(path.read_bytes(), before)

    def test_incomplete_raw_fundamental_schema_fails_explicitly(self):
        from run_pit_audit import load_research_fundamentals
        with TemporaryDirectory() as d:
            path = Path(d) / "fundamentals_hist.csv"
            pd.DataFrame([{"ticker": "AAA", "year": 2022, "period": 12,
                           "paid_in": 100.}]).to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "PIT_FUNDAMENTALS_SCHEMA_UNSUPPORTED"):
                load_research_fundamentals(path)

    def test_non_testable_requested_retest_returns_failure_and_keeps_artifact(self):
        from run_pit_audit import run
        with TemporaryDirectory() as d:
            csv = Path(d) / "membership.csv"
            csv.write_bytes(CSV)
            output = Path(d) / "pit_universe_audit.json"
            args = SimpleNamespace(membership_csv=str(csv), membership_only=False,
                                   start="2017-01-01", end="2017-07-01",
                                   retest=True, output=str(output))
            with patch("run_pit_audit.check_prices",
                       return_value=({}, {"requested": 3, "loaded": 0})):
                with self.assertRaises(SystemExit) as exit_ctx:
                    run(args)
            self.assertEqual(exit_ctx.exception.code, 2)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["exploratory_retest"]["status"], "NOT_TESTABLE")
            self.assertEqual(report["exploratory_retest"]["reason"],
                             "HISTORICAL_PRICE_DATA_UNAVAILABLE")
            self.assertTrue(output.exists())



if __name__ == "__main__":
    unittest.main()
