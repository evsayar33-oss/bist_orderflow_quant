"""Offline regression tests: no market feeds or GitHub credentials."""
import types
import unittest

from research.run_free_challenger_lab import (
    CHALLENGERS, select_candidate, temporary_config,
)


def panel(*, better=True, holdout_delta=4.0):
    result = {}
    for cost in ("1", "2"):
        rows = {}
        for v in CHALLENGERS:
            base = v["name"] == "baseline"
            chosen = v["name"] == "price_breakout"
            premium = (3.0 if chosen else -1.0) if not base else 0.0
            validation = (holdout_delta if chosen else -1.0) if not base else 0.0
            if not better:
                premium = -2.0
            rows[v["name"]] = {
                "full": {"start": "2022-02-01", "end": "2026-09-30"},
                "train": {
                    "start": "2022-02-01", "end": "2023-12-29", "days": 477,
                    "real_cagr_pct": 10.0 + premium,
                    "usd_cagr_pct": 12.0 + premium,
                    "usd_max_drawdown_pct": -30.0,
                    "max_drawdown_pct": -35.0,
                },
                "holdout": {
                    "start": "2024-01-02", "end": "2026-09-30", "days": 690,
                    "real_cagr_pct": 12.0 + validation,
                    "max_drawdown_pct": -30.0,
                    "rolling12m_beat_all_pct": 45.0,
                },
            }
        result[cost] = rows
    return result


class ChallengerGates(unittest.TestCase):
    def test_research_promise_not_live_adoption(self):
        r = select_candidate(panel())
        self.assertEqual(r["candidate"], "price_breakout")
        self.assertEqual(r["historical_holdout_gate"], "PROMISING_UNVERIFIED")
        self.assertFalse(r["live_promotion_allowed"])

    def test_weak_holdout_rejected(self):
        r = select_candidate(panel(holdout_delta=0.0))
        self.assertEqual(r["historical_holdout_gate"], "FAILED")

    def test_training_failure_rejected(self):
        r = select_candidate(panel(better=False))
        self.assertEqual(r["historical_holdout_gate"], "NO_TRAIN_WINNER")

    def test_missing_cost_rejected(self):
        with self.assertRaisesRegex(ValueError, "BOTH_COST_STRESSES"):
            select_candidate({"1": panel()["1"]})

    def test_missing_variant_rejected(self):
        p = panel()
        del p["1"]["price_breakout"]
        with self.assertRaisesRegex(ValueError, "INCOMPLETE_VARIANT_PANEL"):
            select_candidate(p)

    def test_unmatched_window_rejected(self):
        p = panel()
        p["2"]["inverse_vol"]["full"]["end"] = "2025-12-31"
        with self.assertRaisesRegex(ValueError, "UNMATCHED_VARIANT_PERIOD"):
            select_candidate(p)

    def test_temporary_override_restores_existing_and_missing(self):
        cfg = types.SimpleNamespace(A=3)
        with temporary_config(cfg, {"A": 4, "B": 10}):
            self.assertEqual(cfg.A, 4)
            self.assertEqual(cfg.B, 10)
        self.assertEqual(cfg.A, 3)
        self.assertFalse(hasattr(cfg, "B"))

    def test_override_restores_after_error(self):
        cfg = types.SimpleNamespace(COST_ONE_WAY=0.0025)
        with self.assertRaisesRegex(RuntimeError, "test"):
            with temporary_config(cfg, {"COST_ONE_WAY": 0.01}):
                raise RuntimeError("test")
        self.assertEqual(cfg.COST_ONE_WAY, 0.0025)


if __name__ == "__main__":
    unittest.main()
