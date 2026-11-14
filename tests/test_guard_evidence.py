"""Hardened, offline H083-style evidence gates adapted to BIST long-horizon cohorts.

Run: python -m unittest discover -s tests -p test_guard_evidence.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import health
from meta_engine import plan_tranche
from portfolio import new_portfolio
from safety_orders import enforce_pending_gate


class GuardParityTests(unittest.TestCase):
    @staticmethod
    def portfolio():
        pf = new_portfolio("2026-09-01")
        pf["positions"]["OLD"] = {"value": 0.6, "entry_date": "2026-09-02", "level": 0.9}
        pf["cash"] = 0.4
        pf["nav"] = 1.0
        pf["cohorts"] = [{"month": "2026-09", "tickers": ["OLD"], "w": {"OLD": 1.0}}]
        return pf

    def test_safe_blocks_new_buys_without_forced_sale(self):
        pf = self.portfolio()
        before = list(pf["cohorts"])
        orders, status = plan_tranche(
            pf, pd.DataFrame(), {"autonomy_guard": {"mode": "SAFE", "block_new_entries": True,
                                                     "reason": "OPS_DATA_QUALITY"}},
            pd.Timestamp("2026-10-02"))
        self.assertEqual(orders, [])
        self.assertTrue(status["blocked"])
        self.assertEqual(status["holds"], ["OLD"])
        self.assertEqual(pf["cohorts"], before)

    def test_zero_exposure_queues_exits_and_no_ghost_cohort(self):
        pf = self.portfolio()
        orders, status = plan_tranche(
            pf, pd.DataFrame(), {"risk_exposure": 0.0}, pd.Timestamp("2026-10-02"))
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["action"], "SELL")
        self.assertEqual(orders[0]["reason"], "RISK_EXPOSURE_ZERO")
        self.assertEqual(status["buys"], [])
        self.assertEqual(pf["cohorts"], [])

    def test_zero_exposure_wins_even_when_guard_is_safe(self):
        pf = self.portfolio()
        orders, _ = plan_tranche(
            pf, pd.DataFrame(), {"risk_exposure": 0, "autonomy_guard": {
                "mode": "SAFE", "block_new_entries": True}}, pd.Timestamp("2026-10-02"))
        self.assertEqual([o["action"] for o in orders], ["SELL"])

    def test_nonfinite_exposure_fails_closed(self):
        pf = self.portfolio()
        orders, status = plan_tranche(
            pf, pd.DataFrame(), {"risk_exposure": float("nan")}, pd.Timestamp("2026-10-02"))
        self.assertEqual(orders, [])
        self.assertEqual(status["block_reason"], "INVALID_RISK_EXPOSURE")
        self.assertIn("OLD", pf["positions"])


class PendingOrderSafetyTests(unittest.TestCase):
    def test_safe_cancels_old_buys_and_increasing_rebalances(self):
        pf = GuardParityTests.portfolio()
        pf["pending"] = [
            {"ticker": "NEW", "action": "BUY", "target_w": 0.1},
            {"ticker": "OLD", "action": "REBAL", "target_w": 0.8},
            {"ticker": "OLD", "action": "REBAL", "target_w": 0.4},
            {"ticker": "RISK", "action": "SELL", "target_w": 0.0},
        ]
        canceled = enforce_pending_gate(pf, {"mode": "SAFE"})
        self.assertEqual(canceled, 2)
        self.assertEqual([o["action"] for o in pf["pending"]], ["REBAL", "SELL"])
        self.assertEqual(pf["pending"][0]["target_w"], 0.4)

    def test_normal_mode_retains_existing_pending(self):
        pf = GuardParityTests.portfolio()
        pf["pending"] = [{"ticker": "NEW", "action": "BUY", "target_w": 0.1}]
        self.assertEqual(enforce_pending_gate(pf, {"mode": "NORMAL",
                                                    "block_new_entries": False}), 0)
        self.assertEqual(len(pf["pending"]), 1)

    def test_explicit_block_overrides_normal_label(self):
        pf = GuardParityTests.portfolio()
        pf["pending"] = [{"ticker": "NEW", "action": "BUY", "target_w": 0.1}]
        self.assertEqual(enforce_pending_gate(pf, {"mode": "NORMAL",
                                                    "block_new_entries": True}), 1)
        self.assertEqual(pf["pending"], [])


class EvidenceTests(unittest.TestCase):
    @staticmethod
    def frames():
        idx = pd.bdate_range("2020-01-01", periods=850)
        bt = pd.DataFrame({"tarih": idx, "nav": np.linspace(1, 2, len(idx)),
                           "xu100": np.linspace(100, 150, len(idx))})
        ld = pd.bdate_range("2026-08-03", periods=55)
        live = pd.DataFrame({"tarih": ld, "nav": np.linspace(1, 0.90, len(ld)),
                             "xu100": np.linspace(100, 95, len(ld))})
        return live, bt

    def test_descriptive_percentile_includes_sample_and_bias_disclaimer(self):
        live, bt = self.frames()
        res = health.live_vs_test(live, bt)
        self.assertEqual(res["status"], "OK")
        self.assertGreater(res["n_overlapping_windows"], 50)
        self.assertGreater(res["n_nonoverlapping_approx"], 0)
        self.assertEqual(res["comparison_basis"], "BIST100_EXCESS_SAME_AGE_DESCRIPTIVE")
        self.assertIn("surviving", res["bias_notice"])

    def test_missing_live_benchmark_endpoint_never_backfills_from_future(self):
        live, bt = self.frames()
        live.loc[0, "xu100"] = np.nan
        res = health.live_vs_test(live, bt)
        self.assertEqual(res["status"], "WAIT")
        self.assertEqual(res["reason"], "MISSING_LIVE_BENCHMARK_ENDPOINT")

    def test_tiny_live_win_rate_is_not_validated(self):
        state = {
            "data_quality": {"rows": 320}, "inflation": {"status": "OK", "source": "test"},
            "last_eod_date": "2026-11-13",
            "portfolio": {"pending": [], "last_rebalance_month": "2026-11"},
            "autonomy_guard": {"self_test": {"passed": True}},
            "performance": {"lots": {"closed_lots": 1, "hit_nominal_pct": 0.0,
                                      "exit_mix": {"NOT_SELECTED": 1}}},
            "model": {}, "confidence_live": {},
        }
        report = {"generated_at": "2026-11-02", "closed_lots": {
            "closed_lots": 145, "hit_nominal_pct": 90.3, "exit_mix": {"COHORT_EXPIRY": 145}}}
        res = health.evaluate(state, pd.DataFrame(), None, report,
                              pd.Timestamp("2026-11-14"), bt_nav=pd.DataFrame())
        self.assertEqual(res["win_rates"]["comparison_status"], "INSUFFICIENT_LIVE_SAMPLE")
        self.assertIsNone(res["win_rates"]["hit_nominal_pct"]["live"])
        self.assertEqual(res["win_rates"]["hit_nominal_pct"]["test"], 90.3)
        self.assertEqual(res["win_rates"]["closed_lots"]["live"], 1)
        checks = {c["key"]: c for c in res["checks"]}
        self.assertEqual(checks["ic"]["status"], "BEKLEME")
        self.assertEqual(checks["conf"]["status"], "BEKLEME")


if __name__ == "__main__":
    unittest.main()
