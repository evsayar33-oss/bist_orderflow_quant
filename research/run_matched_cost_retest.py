"""Research-only matched-window legacy/PIT and transaction-cost stress.

Reuses one downloaded price dictionary and one fundamentals cache; NEVER saves
production NAV, prior, model, or order state.  These are scenario assumptions,
not independently measured exchange spreads or delisting recoveries.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import traceback

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pit_universe import Membership, download_membership, save_json
from run_pit_audit import check_prices, load_research_fundamentals
from pit_evidence import assess_evidence


@contextmanager
def assumed_round_trip_cost(config, percent):
    if not (0 <= percent <= 10):
        raise ValueError("INVALID_RESEARCH_COST_PERCENT")
    original_one_way, original_round_trip = config.COST_ONE_WAY, config.COST_ROUND_TRIP_PCT
    try:
        config.COST_ROUND_TRIP_PCT = float(percent)
        config.COST_ONE_WAY = float(percent) / 200.0
        yield
    finally:
        config.COST_ONE_WAY = original_one_way
        config.COST_ROUND_TRIP_PCT = original_round_trip


def comparable_metrics(report):
    portfolio = report.get("portfolio") or {}
    lots = report.get("closed_lots") or {}
    return {"period": report.get("period"), "portfolio": portfolio,
            "closed_lots": lots, "rebalance_months": report.get("rebalance_months"),
            "universe_downloaded": report.get("universe_downloaded"),
            "cost_round_trip_pct": report.get("cost_round_trip_pct"),
            "pit_membership_filter_applied": report.get("pit_membership_filter_applied")}


def evaluate_scenarios(engine, config, *, bars, pit, membership, start, end,
                       costs=(0.5, 1.0, 2.0)):
    """Return scenarios only after all runs succeed; no silent partial success."""
    if len(costs) != len(set(costs)) or any(c < 0 or c > 10 for c in costs):
        raise ValueError("INVALID_COST_SCENARIOS")
    results = {}
    for label, universe in (("legacy_current_membership", None),
                            ("historical_snapshot_membership", membership)):
        for cost in costs:
            with assumed_round_trip_cost(config, cost):
                output = engine.run(start=start, end=end, save=False, data=bars,
                                    pit=pit, pit_membership=universe)
            results[f"{label}__round_trip_{cost:g}pct"] = comparable_metrics(output["report"])
    periods = {r["period"] for r in results.values()}
    if len(periods) != 1:
        raise RuntimeError("MATCHED_WINDOW_PERIOD_MISMATCH: " + repr(sorted(periods)))
    return results


def run(args):
    report = {"schema": "BIST_MATCHED_COST_RESEARCH_V1",
              "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "status": "NOT_TESTABLE", "production_state_changed": False,
              "survivorship_bias_resolved": False, "validated_alpha": False,
              "execution_costs_independently_verified": False,
              "limitations": [
                  "Same OHLCV and fundamental cache; membership eligibility differs intentionally",
                  "Legacy universe here means no historical membership filter, NOT a reproduction of the old 2017-2026 production run",
                  "Round-trip costs are assumed stress levels, NOT measured bid-ask or actual fills",
                  "Missing historical prices, delisting recoveries, filing timestamps and revision vintages remain unverified",
                  "PIT and legacy samples can differ in eligible names; do not attribute all performance difference to survivorship bias",
              ]}
    try:
        import config as C
        import backtest_optimizer as B
        frame, source = download_membership()
        members = Membership(frame)
        bars, prices = check_prices(members, args.start, args.end, download=True)
        audit = members.audit(args.start, args.end, bars)
        pit, fundamentals = load_research_fundamentals(Path(C.FUNDAMENTALS_CACHE_FILE))
        costs = tuple(float(x) for x in args.costs.split(","))
        report.update({"membership_source": source, "price_query": prices,
                       "coverage": {"historical_tickers": audit["historical_tickers"],
                                    "priced": audit["symbols_with_any_price_series"],
                                    "missing": audit["symbols_without_price_series"],
                                    "months_below_95pct": audit["months_below_95pct_price_coverage_count"]},
                       "fundamentals": fundamentals,
                       "evidence_gates": assess_evidence(audit, fundamentals,
                                                        Path(args.evidence_dir)),
                       "cost_scenarios_pct": costs})
        results = evaluate_scenarios(B, C, bars=bars, pit=pit, membership=members,
                                     start=args.start, end=args.end, costs=costs)
        report["scenarios"] = results
        report["status"] = "EXPLORATORY_INCOMPLETE_PIT"
        report["comparison_window"] = next(iter(results.values()))["period"]
    except Exception as exc:
        report.update({"status": "NOT_TESTABLE", "error_type": type(exc).__name__,
                       "error_detail": str(exc)[:400],
                       "traceback": traceback.format_exc(limit=12)[-5000:]})
    finally:
        save_json(args.output, report)
    if report["status"] == "NOT_TESTABLE":
        raise SystemExit(2)
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2017-01-01")
    p.add_argument("--end", default="2026-10-01")
    p.add_argument("--costs", default="0.5,1.0,2.0")
    p.add_argument("--evidence-dir", default="research/evidence")
    p.add_argument("--output", default="artifacts/pit_matched_cost_retest.json")
    run(p.parse_args())
