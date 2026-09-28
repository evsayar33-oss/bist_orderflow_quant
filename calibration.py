"""Score -> expected trade return calibration and cut-off selection.

Replaces V1's "maximise T+3 win rate" optimiser. The objective is now the
expected NET return of the actual trade (triple-barrier, after costs),
estimated with date-clustered Newey-West errors, and the cut-off is chosen by
the lower confidence bound (mean - 1.28*se), i.e. conservatively.

Absolute gate: cross-sectional scores only say "better than peers". Whether a
trade is worth taking also depends on the market. So

    E[net trade return] = E[market over the hold | HMM forecast]
                        + E[excess return | score percentile]   (calibrated)
                        - round-trip cost

and a name is eligible only if this is above MIN_EXPECTED_EDGE_PCT.
Calibration uses the composite AS RECORDED LIVE (or OOS in the backtest), so it
is genuinely out-of-sample.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import config as C
from learner_engine import newey_west

BUCKETS = [(0.0, 50.0), (50.0, 70.0), (70.0, 80.0), (80.0, 90.0), (90.0, 95.0), (95.0, 100.01)]
PRIOR_SE_INFLATION = 2.0     # research prior is from a different (smaller) universe
Z_LCB = 1.2816


def add_excess(df: pd.DataFrame) -> pd.DataFrame:
    d = df.dropna(subset=["barrier_gross"]).copy()
    d["excess"] = d["barrier_gross"] - d.groupby("tarih")["barrier_gross"].transform("mean")
    return d


def _clustered(d: pd.DataFrame, col: str) -> Dict:
    per_date = d.groupby("tarih")[col].mean()
    m, se, t, n = newey_west(per_date, C.MAX_HOLD - 1)
    return {"mean": float(m), "se": float(se) if np.isfinite(se) else 99.0, "n_dates": int(n), "n": int(len(d))}


def bucket_table(d: pd.DataFrame) -> List[Dict]:
    out = []
    for lo, hi in BUCKETS:
        sub = d[(d["composite_pct"] >= lo) & (d["composite_pct"] < hi)]
        st = _clustered(sub, "excess") if len(sub) else {"mean": 0.0, "se": 99.0, "n_dates": 0, "n": 0}
        out.append({"lo": lo, "hi": hi, "mean_excess": st["mean"], "se": st["se"],
                    "n_dates": st["n_dates"], "n": st["n"]})
    return out


def cutoff_stats(d: pd.DataFrame, cutoffs=None) -> Dict[str, Dict]:
    cutoffs = cutoffs or C.PCT_CUTOFF_CANDIDATES
    res = {}
    for c in cutoffs:
        sub = d[d["composite_pct"] >= c]
        if sub.empty:
            res[str(c)] = {"mean": 0.0, "se": 99.0, "n_dates": 0, "n": 0}
            continue
        st = _clustered(sub, "excess")
        st["hit_rate"] = round(float((sub["barrier_gross"] > C.COST_ROUND_TRIP_PCT).mean() * 100.0), 2)
        st["mean_net"] = round(float(sub["barrier_gross"].mean() - C.COST_ROUND_TRIP_PCT), 4)
        res[str(c)] = st
    return res


def _combine(live: Optional[Dict], prior: Optional[Dict], mean_key: str = "mean") -> Dict:
    """Normal-normal precision-weighted combination."""
    parts = []
    if live and live.get("n_dates", 0) >= 5 and np.isfinite(live.get("se", np.inf)) and live["se"] < 50:
        parts.append((live[mean_key], live["se"]))
    if prior and prior.get("n_dates", 0) >= 5 and np.isfinite(prior.get("se", np.inf)) and prior["se"] < 50:
        parts.append((prior[mean_key], prior["se"] * PRIOR_SE_INFLATION))
    if not parts:
        return {"mean": 0.0, "se": 99.0}
    prec = np.array([1.0 / max(se, 1e-6) ** 2 for _, se in parts])
    means = np.array([m for m, _ in parts])
    m = float((prec * means).sum() / prec.sum())
    return {"mean": m, "se": float(np.sqrt(1.0 / prec.sum()))}


def calibrate(state: Dict, dataset: pd.DataFrame, research: Optional[Dict]) -> Dict:
    cal = state.setdefault("calibration", {})
    prior = (research or {}).get("calibration", {}) if research else {}
    live_buckets, live_cuts = [], {}
    if dataset is not None and not dataset.empty and {"composite_pct", "barrier_gross"} <= set(dataset.columns):
        d = add_excess(dataset.dropna(subset=["composite_pct"]))
        if not d.empty:
            live_buckets = bucket_table(d)
            live_cuts = cutoff_stats(d)

    table = []
    pb = {(b["lo"], b["hi"]): b for b in prior.get("buckets", [])} if prior else {}
    for i, (lo, hi) in enumerate(BUCKETS):
        lb = live_buckets[i] if live_buckets else None
        post = _combine({"mean": lb["mean_excess"], "se": lb["se"], "n_dates": lb["n_dates"]} if lb else None,
                        {"mean": pb[(lo, hi)]["mean_excess"], "se": pb[(lo, hi)]["se"],
                         "n_dates": pb[(lo, hi)]["n_dates"]} if (lo, hi) in pb else None)
        table.append({"lo": lo, "hi": hi, "mean_excess": round(post["mean"], 4), "se": round(post["se"], 4),
                      "live_n_dates": lb["n_dates"] if lb else 0})

    best_c, best_lcb, cut_report = C.DEFAULT_PCT_CUTOFF, -np.inf, {}
    pc = prior.get("cutoffs", {}) if prior else {}
    any_evidence = False
    for c in C.PCT_CUTOFF_CANDIDATES:
        post = _combine(live_cuts.get(str(c)), pc.get(str(c)))
        lcb = post["mean"] - Z_LCB * post["se"]
        cut_report[str(c)] = {"mean_excess": round(post["mean"], 4), "se": round(post["se"], 4), "lcb": round(lcb, 4),
                              "live": live_cuts.get(str(c))}
        if post["se"] < 50:
            any_evidence = True
            if lcb > best_lcb:
                best_c, best_lcb = c, lcb
    cal["pct_cutoff"] = float(best_c if any_evidence else C.DEFAULT_PCT_CUTOFF)
    cal["bucket_table"] = table
    cal["cutoffs"] = cut_report
    cal["source"] = ("live+research" if live_cuts and prior else "live" if live_cuts else "research" if prior else "default")
    cal["status"] = "CALIBRATED" if any_evidence else "DEFAULT_CUTOFF"
    return state


def expected_excess(pct, table: List[Dict]) -> np.ndarray:
    pct = np.asarray(pct, float)
    out = np.zeros_like(pct)
    if not table:
        return out
    for b in table:
        m = (pct >= b["lo"]) & (pct < b["hi"])
        out[m] = float(b.get("mean_excess", 0.0))
    return out
