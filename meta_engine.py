"""Scoring and selection for Meta-Engine V2.

Pipeline for one EOD snapshot:
  factors (flow_engine) -> champion weights (regime-blended) -> composite
  -> percentile -> calibrated expected net trade return (calibration + HMM)
  -> eligibility gates -> risk-based position size.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

import config as C
from calibration import expected_excess
from flow_engine import build_factor_frame, composite
from labels import stop_distance_pct


def active_weights(state: Dict) -> Tuple[Dict[str, float], str]:
    model = state.get("model", {})
    champ = model.get("champion_weights") or {k: v for k, v in C.PRIOR_IC.items()}
    reg = state.get("regime", {})
    label = reg.get("label")
    p = float((reg.get("probs") or {}).get(label, 0.0) or 0.0)
    rw = (model.get("regime_weights") or {}).get(label)
    if rw and p >= 0.6 and not reg.get("degraded", True):
        w = {k: p * rw.get(k, 0.0) + (1 - p) * champ.get(k, 0.0) for k in C.FACTORS}
        tot = sum(abs(v) for v in w.values()) or 1.0
        return {k: v / tot for k, v in w.items()}, f"{model.get('champion_version', 'prior')}+{label}"
    tot = sum(abs(v) for v in champ.values()) or 1.0
    return {k: v / tot for k, v in champ.items()}, model.get("champion_version", "prior")


def score_snapshot(snap: pd.DataFrame, state: Dict, prev_takas: Optional[pd.Series] = None,
                   guard: Optional[Dict] = None, open_positions: int = 0,
                   held_tickers: Optional[set] = None) -> Tuple[pd.DataFrame, Dict]:
    frame, coverage = build_factor_frame(snap, prev_takas)
    weights, version = active_weights(state)
    frame["composite"] = composite(frame, weights)
    frame["composite_pct"] = frame["composite"].rank(pct=True) * 100.0
    frame["model_version"] = version

    reg = state.get("regime", {})
    cal = state.get("calibration", {})
    exp_mkt = float(reg.get("exp_mkt_5d_pct", 0.0) or 0.0)
    p_off = reg.get("p_risk_off")
    p_off = 0.5 if p_off is None else float(p_off)
    calibrated = cal.get("status") == "CALIBRATED"
    frame["exp_excess_pct"] = expected_excess(frame["composite_pct"], cal.get("bucket_table", [])) if calibrated else 0.0
    frame["exp_net_pct"] = exp_mkt + frame["exp_excess_pct"] - C.COST_ROUND_TRIP_PCT
    frame["regime_label"] = reg.get("label", "UNKNOWN")
    frame["p_risk_off"] = p_off
    frame["stop_dist_pct"] = stop_distance_pct(frame["atr_pct"])

    guard = guard or {}
    cutoff = float(cal.get("pct_cutoff", C.DEFAULT_PCT_CUTOFF)) + float(guard.get("pct_cutoff_add", 0.0))
    cutoff = min(cutoff, 99.0)
    edge_ok = (frame["exp_net_pct"] >= C.MIN_EXPECTED_EDGE_PCT) if calibrated else (exp_mkt >= 0.0)
    elig = (
        (frame["composite_pct"] >= cutoff)
        & edge_ok
        & (pd.to_numeric(frame["value_traded"], errors="coerce") >= C.MIN_VALUE_TRADED_TL)
        & (pd.to_numeric(frame["change_pct"], errors="coerce") < C.LIMIT_MOVE_PCT)   # closed at limit-up: no fill
        & np.isfinite(frame["atr_pct"])
    )
    if held_tickers:
        elig &= ~frame["ticker"].isin(held_tickers)
    if guard.get("block_new_entries"):
        elig &= False

    regime_mult = float(np.clip(1.0 - p_off, 0.25, 1.0))
    exposure = float(guard.get("exposure_multiplier", 1.0)) * regime_mult * (1.0 if calibrated else 0.5)
    max_n = int(np.ceil(C.MAX_CANDIDATES * min(1.0, max(exposure, 0.0))))
    max_n = max(0, min(max_n, C.MAX_OPEN_POSITIONS - int(open_positions)))
    frame["eligible"] = False
    if max_n > 0 and elig.any():
        idx = frame.loc[elig].sort_values(["exp_net_pct", "composite"], ascending=False).head(max_n).index
        frame.loc[idx, "eligible"] = True
    base_size = np.minimum(C.MAX_POSITION_PCT, C.RISK_PER_TRADE_PCT / frame["stop_dist_pct"] * 100.0)
    frame["size_pct"] = np.where(frame["eligible"], np.round(base_size * exposure, 2), 0.0)

    info = {
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "model_version": version,
        "coverage": {k: round(v, 3) for k, v in coverage.items()},
        "pct_cutoff": cutoff,
        "calibrated": calibrated,
        "exp_mkt_5d_pct": exp_mkt,
        "exposure": round(exposure, 3),
        "max_candidates": max_n,
        "open_positions": int(open_positions),
        "n_eligible": int(frame["eligible"].sum()),
    }
    return frame.sort_values(["eligible", "composite"], ascending=False).reset_index(drop=True), info
