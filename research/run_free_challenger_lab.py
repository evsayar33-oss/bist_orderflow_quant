"""Free, research-only challenger sweep on one historical membership panel.

No production file writes, orders or automatic promotion. Historical PIT evidence
is incomplete and a 2024+ retrospective slice is not genuinely unseen data.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import math
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CHALLENGERS = (
    {"name": "baseline"},
    {"name": "price_breakout", "EXTRA_BLEND": {"high_52w": 0.5, "trend": 0.25}},
    {"name": "lottery_control", "LOTTERY_BLEND": 0.35},
    {"name": "diversified_8", "TRANCHE_N": 8, "MAX_NAME_W": 0.10},
    {"name": "inverse_vol", "TRANCHE_WEIGHTING": "inv_vol"},
    {"name": "quality_lowvol", "EXTRA_BLEND": {
        "sector_mom": 0.25, "ep_x_quality": 1.0,
        "quality_mom": 0.25, "quality_lowvol": 0.25}},
)
COSTS = (1.0, 2.0)
TRAIN_END = "2023-12-31"
HOLDOUT_START = "2024-01-01"


@contextmanager
def temporary_config(module, overrides):
    missing = object()
    previous = {key: getattr(module, key, missing) for key in overrides}
    try:
        for key, value in overrides.items():
            setattr(module, key, value)
        yield
    finally:
        for key, value in previous.items():
            if value is missing:
                delattr(module, key)
            else:
                setattr(module, key, value)


def _number(data, field):
    value = data.get(field)
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def _calmar(metrics):
    ret = _number(metrics, "usd_cagr_pct")
    dd = _number(metrics, "usd_max_drawdown_pct")
    return ret / abs(dd) if ret is not None and dd is not None and dd < -0.01 else None


def select_candidate(by_cost):
    """Pick with training slice only; evaluate the locked choice on 2024+ and 2% cost.

    This is retrospective model selection, never a live-promotion certification.
    """
    required = ("1", "2")
    if any(k not in by_cost for k in required):
        raise ValueError("BOTH_COST_STRESSES_REQUIRED")
    names = {v["name"] for v in CHALLENGERS}
    for cost in required:
        if set(by_cost[cost]) != names:
            raise ValueError("INCOMPLETE_VARIANT_PANEL_" + cost)
        reference = by_cost[cost]["baseline"]
        start, end = reference["full"]["start"], reference["full"]["end"]
        for name, row in by_cost[cost].items():
            if (row["full"]["start"], row["full"]["end"]) != (start, end):
                raise ValueError("UNMATCHED_VARIANT_PERIOD_" + name)
            if row["train"]["end"] > TRAIN_END or row["holdout"]["start"] < HOLDOUT_START:
                raise ValueError("INVALID_CAUSAL_SPLIT")
            if row["train"]["days"] < 300 or row["holdout"]["days"] < 300:
                raise ValueError("INSUFFICIENT_SPLIT_SESSIONS")
    candidates = []
    base = by_cost["1"]["baseline"]["train"]
    base_real = _number(base, "real_cagr_pct")
    base_calmar = _calmar(base)
    if base_real is None or base_calmar is None:
        return {"candidate": None, "historical_holdout_gate": "NOT_EVALUABLE",
                "reason": "BASELINE_METRICS_MISSING", "live_promotion_allowed": False}
    for name in sorted(names - {"baseline"}):
        trial = by_cost["1"][name]["train"]
        real, calmar = _number(trial, "real_cagr_pct"), _calmar(trial)
        if real is not None and calmar is not None and real >= base_real + 2 and calmar >= base_calmar:
            candidates.append((calmar - base_calmar, real - base_real, name))
    if not candidates:
        return {"candidate": None, "historical_holdout_gate": "NO_TRAIN_WINNER",
                "reason": "NO_CANDIDATE_BEATS_BASELINE_IN_PRE_2024_WINDOW",
                "live_promotion_allowed": False}
    selected = max(candidates)[2]
    comparisons = {}
    pass_all = True
    for cost in required:
        ref = by_cost[cost]["baseline"]["holdout"]
        new = by_cost[cost][selected]["holdout"]
        ref_real, trial_real = _number(ref, "real_cagr_pct"), _number(new, "real_cagr_pct")
        ref_dd, trial_dd = _number(ref, "max_drawdown_pct"), _number(new, "max_drawdown_pct")
        ref_win, trial_win = _number(ref, "rolling12m_beat_all_pct"), _number(new, "rolling12m_beat_all_pct")
        ok = (None not in (ref_real, trial_real, ref_dd, trial_dd, ref_win, trial_win)
              and trial_real >= ref_real + 2.0
              and trial_dd >= ref_dd - 3.0
              and trial_win >= ref_win - 5.0)
        comparisons[cost] = {
            "meets_conservative_thresholds": bool(ok),
            "real_cagr_delta_pp": None if ref_real is None or trial_real is None else round(trial_real - ref_real, 2),
            "max_drawdown_delta_pp": None if ref_dd is None or trial_dd is None else round(trial_dd - ref_dd, 2),
            "rolling_beat_all_delta_pp": None if ref_win is None or trial_win is None else round(trial_win - ref_win, 2),
        }
        pass_all &= bool(ok)
    return {"candidate": selected,
            "historical_holdout_gate": "PROMISING_UNVERIFIED" if pass_all else "FAILED",
            "cost_comparisons": comparisons,
            "live_promotion_allowed": False,
            "reason": "RETROSPECTIVE_AND_INCOMPLETE_PIT_DATA"}


def _capture_simulations(engine, config, cost, captured):
    import pandas as pd

    def collector(variants, simulate, cpi, bench):
        rows = {}
        for variant in variants:
            name = variant["name"]
            overrides = {k: v for k, v in variant.items() if k != "name"}
            with temporary_config(config, overrides):
                _portfolio, nav, _closed, _names = simulate()
            if nav.empty or nav["nav"].isna().any() or (nav["nav"] <= 0).any():
                raise ValueError("INVALID_NAV_" + name)
            nav = nav.sort_values("tarih").copy()
            nav["tarih"] = pd.to_datetime(nav["tarih"])
            train = nav[nav["tarih"] <= TRAIN_END]
            holdout = nav[nav["tarih"] >= HOLDOUT_START]
            if len(train) < 300 or len(holdout) < 300:
                raise ValueError("INSUFFICIENT_TRAIN_OR_HOLDOUT_" + name)
            rows[name] = {
                "full": engine.nav_metrics(nav, cpi, bench),
                "train": engine.nav_metrics(train, cpi, bench),
                "holdout": engine.nav_metrics(holdout, cpi, bench),
            }
            print("CHALLENGER", cost, name, "holdout-real-CAGR",
                  rows[name]["holdout"].get("real_cagr_pct"), flush=True)
        captured.update(rows)
        return list(rows.values())
    return collector


def run(args):
    from pit_universe import Membership, download_membership, save_json
    from run_pit_audit import check_prices, load_research_fundamentals
    import backtest_optimizer as engine
    import config as config

    result = {
        "schema": "BIST_FREE_CHALLENGERS_V1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": "NOT_TESTABLE",
        "production_changed": False,
        "validated_alpha": False,
        "live_promotion_allowed": False,
        "historical_pit_fully_verified": False,
        "selection_warning": "2024+ is a retrospective holdout, not genuinely unseen live data",
        "filing_warning": "Historical filing availability uses assumed lags; revisions unverified",
        "execution_warning": "Costs are hypothetical round-trip assumptions, not measured fills",
        "candidate_rules": list(CHALLENGERS),
        "costs_round_trip_pct": list(COSTS),
        "train_through": TRAIN_END,
        "holdout_from": HOLDOUT_START,
    }
    try:
        frame, source = download_membership()
        members = Membership(frame)
        bars, price_meta = check_prices(members, args.start, args.end, download=True)
        audit = members.audit(args.start, args.end, bars)
        pit, fundamental_meta = load_research_fundamentals(Path(config.FUNDAMENTALS_CACHE_FILE))
        result["data_evidence"] = {
            "membership": source,
            "historical_tickers": audit["historical_tickers"],
            "symbols_without_prices": audit["symbols_without_price_series"],
            "months_below_95pct_coverage": audit["months_below_95pct_price_coverage_count"],
            "fundamentals": fundamental_meta,
        }
        result["price_query"] = price_meta
        by_cost = {}
        periods = []
        original = engine.run_variants
        try:
            for cost in COSTS:
                captured = {}
                engine.run_variants = _capture_simulations(engine, config, cost, captured)
                with temporary_config(config, {
                    "COST_ROUND_TRIP_PCT": cost,
                    "COST_ONE_WAY": cost / 200.0,
                }):
                    report = engine.run(start=args.start, end=args.end, save=False, data=bars,
                                        pit=pit, pit_membership=members,
                                        variants=list(CHALLENGERS))["report"]
                if set(captured) != {v["name"] for v in CHALLENGERS}:
                    raise ValueError("CHALLENGER_PANEL_INCOMPLETE")
                periods.append(report.get("period"))
                by_cost[f"{cost:g}"] = captured
        finally:
            engine.run_variants = original
        if len(set(periods)) != 1 or not periods[0]:
            raise ValueError("COST_STRESSES_NOT_MATCHED")
        result["matched_period"] = periods[0]
        result["results"] = by_cost
        result["decision"] = select_candidate(by_cost)
        result["status"] = "EXPLORATORY_UNVERIFIED_PIT"
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error_detail"] = str(exc)[:500]
        result["traceback"] = traceback.format_exc(limit=12)[-4000:]
    finally:
        save_json(args.output, result)
    if result["status"] == "NOT_TESTABLE":
        raise SystemExit(2)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2017-01-01")
    parser.add_argument("--end", default="2026-10-01")
    parser.add_argument("--output", default="artifacts/free_challenger_lab.json")
    run(parser.parse_args())
