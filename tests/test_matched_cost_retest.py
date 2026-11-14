"""Offline regression tests for research-only matched cost scenarios."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
from run_matched_cost_retest import assumed_round_trip_cost, evaluate_scenarios


class CostScenarioTests(unittest.TestCase):
    def test_cost_is_restored_even_on_failure(self):
        cfg = SimpleNamespace(COST_ONE_WAY=.0025, COST_ROUND_TRIP_PCT=.5)
        with self.assertRaisesRegex(RuntimeError, "expected"):
            with assumed_round_trip_cost(cfg, 2.):
                self.assertAlmostEqual(cfg.COST_ONE_WAY, .01)
                self.assertEqual(cfg.COST_ROUND_TRIP_PCT, 2.)
                raise RuntimeError("expected")
        self.assertEqual(cfg.COST_ONE_WAY, .0025)
        self.assertEqual(cfg.COST_ROUND_TRIP_PCT, .5)

    def test_six_scenarios_share_window_and_never_save(self):
        cfg = SimpleNamespace(COST_ONE_WAY=.0025, COST_ROUND_TRIP_PCT=.5)
        calls = []
        def run(**kw):
            calls.append((kw["save"], kw["pit_membership"], cfg.COST_ROUND_TRIP_PCT,
                          kw["data"], kw["pit"]))
            return {"report": {"period": "2022-02-01..2026-09-30",
                               "portfolio": {"cagr_nominal_pct": 5.},
                               "closed_lots": {"win_rate_pct": 50.},
                               "cost_round_trip_pct": cfg.COST_ROUND_TRIP_PCT,
                               "pit_membership_filter_applied": kw["pit_membership"] is not None}}
        engine = SimpleNamespace(run=run)
        universe, bars, pit = object(), object(), object()
        results = evaluate_scenarios(engine, cfg, bars=bars, pit=pit,
                                     membership=universe, start="2017-01-01",
                                     end="2026-10-01")
        self.assertEqual(len(results), 6)
        self.assertTrue(all(not x[0] and x[3] is bars and x[4] is pit for x in calls))
        self.assertEqual([x[2] for x in calls], [.5, 1., 2., .5, 1., 2.])
        self.assertTrue(all(x[1] is None for x in calls[:3]))
        self.assertTrue(all(x[1] is universe for x in calls[3:]))
        self.assertEqual(cfg.COST_ONE_WAY, .0025)

    def test_mismatched_window_is_rejected(self):
        cfg = SimpleNamespace(COST_ONE_WAY=.0025, COST_ROUND_TRIP_PCT=.5)
        def run(**kw):
            return {"report": {"period": "A" if kw["pit_membership"] is None else "B"}}
        with self.assertRaisesRegex(RuntimeError, "MATCHED_WINDOW_PERIOD_MISMATCH"):
            evaluate_scenarios(SimpleNamespace(run=run), cfg, bars={}, pit={},
                               membership=object(), start="2017-01-01",
                               end="2026-10-01", costs=(.5,))

    def test_invalid_costs_are_rejected(self):
        cfg = SimpleNamespace(COST_ONE_WAY=.0025, COST_ROUND_TRIP_PCT=.5)
        with self.assertRaisesRegex(ValueError, "INVALID_COST"):
            evaluate_scenarios(None, cfg, bars={}, pit={}, membership=None,
                               start="2017-01-01", end="2026-10-01", costs=(-1.,))
