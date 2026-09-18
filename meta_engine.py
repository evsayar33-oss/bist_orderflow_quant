"""Adaptive regime-aware Meta-Engine V1.

The engine separates observation from outcome:
- Current price movement is not a hard positive gate.
- Pre-move score is built from flow/activity/absorption/structure.
- Outcomes are learned later from future trading sessions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from data_integrity import data_quality_score, apply_explicit_imputation
from flow_engine import build_flow_features

BASE_WEIGHTS = {
    "pre_move": 0.40,
    "flow": 0.25,
    "resilience": 0.15,
    "regime_fit": 0.10,
    "quality": 0.10,
}

REGIME_PROFILES = {
    "BULL_EXPANSION": {"pre_move": 0.35, "flow": 0.30, "resilience": 0.15, "regime_fit": 0.10, "quality": 0.10},
    "BULL_SELECTIVE": {"pre_move": 0.40, "flow": 0.30, "resilience": 0.15, "regime_fit": 0.05, "quality": 0.10},
    "ROTATION": {"pre_move": 0.38, "flow": 0.32, "resilience": 0.15, "regime_fit": 0.05, "quality": 0.10},
    "NEUTRAL": BASE_WEIGHTS,
    "RISK_OFF": {"pre_move": 0.30, "flow": 0.25, "resilience": 0.25, "regime_fit": 0.10, "quality": 0.10},
    "BROAD_SELL_OFF": {"pre_move": 0.28, "flow": 0.25, "resilience": 0.27, "regime_fit": 0.10, "quality": 0.10},
    "PANIC": {"pre_move": 0.25, "flow": 0.20, "resilience": 0.30, "regime_fit": 0.15, "quality": 0.10},
    "RECOVERY": {"pre_move": 0.42, "flow": 0.25, "resilience": 0.13, "regime_fit": 0.10, "quality": 0.10},
}


def detect_market_regime(df: pd.DataFrame):
    daily = pd.to_numeric(df["change_%"], errors="coerce").fillna(0.0)
    weekly = pd.to_numeric(df["perf_w"], errors="coerce").fillna(0.0)
    monthly = pd.to_numeric(df["perf_1m"], errors="coerce").fillna(0.0)
    rvol = pd.to_numeric(df["rvol"], errors="coerce").fillna(1.0)
    breadth_up = float((daily > 0).mean())
    breadth_down = float((daily < 0).mean())
    median_daily = float(daily.median())
    median_monthly = float(monthly.median())
    dispersion = float(daily.std(ddof=0)) if len(daily) > 1 else 0.0
    vol_stress = float((rvol >= 1.5).mean())

    if breadth_down >= 0.80 and median_daily <= -4.0:
        regime = "PANIC"
    elif breadth_down >= 0.65 and median_daily <= -1.5:
        regime = "BROAD_SELL_OFF"
    elif breadth_down >= 0.52 and median_monthly < -3.0:
        regime = "RISK_OFF"
    elif breadth_up <= 0.48 and median_daily <= -0.3 and median_monthly >= 1.0:
        regime = "ROTATION"
    elif breadth_up >= 0.72 and median_monthly >= 2.0:
        regime = "BULL_EXPANSION"
    elif breadth_up >= 0.55 and median_monthly >= 0.0:
        regime = "BULL_SELECTIVE"
    elif median_daily > 0.5 and breadth_up > 0.50 and median_monthly < 0.0:
        regime = "RECOVERY"
    else:
        regime = "NEUTRAL"

    agreement = 0.0
    if breadth_up >= 0.65 or breadth_down >= 0.65:
        agreement += 0.40
    if abs(median_daily) >= 0.5:
        agreement += 0.20
    if abs(median_monthly) >= 2.0:
        agreement += 0.20
    if dispersion < 2.5 or dispersion > 5.0:
        agreement += 0.10
    if vol_stress >= 0.20:
        agreement += 0.10
    confidence = round(float(min(0.98, max(0.35, agreement))) * 100.0, 1)

    return regime, confidence, {
        "breadth_up": round(breadth_up, 4),
        "breadth_down": round(breadth_down, 4),
        "median_daily": round(median_daily, 3),
        "median_monthly": round(median_monthly, 3),
        "dispersion": round(dispersion, 3),
        "high_rvol_share": round(vol_stress, 4),
    }


def _rank(s):
    s = pd.to_numeric(s, errors="coerce")
    if s.notna().sum() <= 1:
        return pd.Series(50.0, index=s.index)
    return (s.rank(pct=True, method="average").fillna(0.5) * 100).clip(0, 100)


def score_market(df: pd.DataFrame, state: dict) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    # Only secondary fundamentals are explicitly imputed; core market facts are not fabricated.
    df2, imputed = apply_explicit_imputation(df, ["roe", "pb", "pe", "oper_margin", "high_1m", "low_1m", "market_cap"])
    f = build_flow_features(df2)

    regime, confidence, regime_stats = detect_market_regime(f)
    state["market_regime"] = regime
    state["regime_confidence"] = confidence
    state["regime_stats"] = regime_stats

    # Market-relative resilience: contextual, not a hard positive-price gate.
    excess_vs_market = pd.to_numeric(f["change_%"], errors="coerce").fillna(0.0) - float(f["change_%"].median())
    rel_week = pd.to_numeric(f["perf_w"], errors="coerce").fillna(0.0)
    rel_month = pd.to_numeric(f["perf_1m"], errors="coerce").fillna(0.0)
    resilience = (
        _rank(excess_vs_market) * 0.30
        + _rank(rel_week) * 0.20
        + _rank(rel_month) * 0.20
        + _rank(f["rvol"]) * 0.10
        + f["pct_takas"] * 0.10
        + f["pct_liquidity"] * 0.10
    ).clip(0, 100).round(1)

    f["resilience_score"] = resilience
    # Rejim-özel uyum: aynı sinyal her market koşulunda aynı anlamı taşımaz.
    if regime in ("PANIC", "BROAD_SELL_OFF", "RISK_OFF"):
        fit_raw = f["resilience_score"] * 0.55 + f["flow_score"] * 0.25 + f["pre_move_score"] * 0.20
    elif regime in ("BULL_EXPANSION", "BULL_SELECTIVE", "RECOVERY"):
        fit_raw = f["pre_move_score"] * 0.50 + f["flow_score"] * 0.35 + f["resilience_score"] * 0.15
    elif regime == "ROTATION":
        fit_raw = f["flow_score"] * 0.45 + f["resilience_score"] * 0.35 + f["pre_move_score"] * 0.20
    else:
        fit_raw = f["pre_move_score"] * 0.45 + f["flow_score"] * 0.30 + f["resilience_score"] * 0.25
    f["regime_fit_score"] = fit_raw.clip(0, 100).round(1)

    f["quality_score"] = data_quality_score(f)
    f.loc[imputed, "quality_score"] = (f.loc[imputed, "quality_score"] - 8.0).clip(0, 100)
    f["risk_score"] = (
        f["structural_risk"] * 0.40
        + (100.0 - f["pct_liquidity"]) * 0.20
        + (100.0 - f["quality_score"]) * 0.25
        + (100.0 - f["pct_absorption"]) * 0.15
    ).clip(0, 100).round(1)
    f["overnight_risk"] = (
        f["structural_risk"] * 0.35
        + (100.0 - f["pct_liquidity"]) * 0.25
        + (100.0 - f["quality_score"]) * 0.20
        + (100.0 - f["resilience_score"]) * 0.20
    ).clip(0, 100).round(1)

    active_weights = REGIME_PROFILES.get(regime, BASE_WEIGHTS).copy()
    regime_learned = state.get("regime_weights", {}).get(regime, {})
    learned = regime_learned if regime_learned else state.get("weights", {})
    # Learned weights move the regime prior, but never replace it completely.
    merged = {}
    for k in BASE_WEIGHTS:
        prior = active_weights[k]
        learned_w = float(learned.get(k, prior))
        merged[k] = 0.65 * prior + 0.35 * learned_w
    total = sum(merged.values()) or 1.0
    merged = {k: v / total for k, v in merged.items()}
    state["effective_weights"] = {k: round(v, 4) for k, v in merged.items()}

    f["meta_score"] = (
        f["pre_move_score"] * merged["pre_move"]
        + f["flow_score"] * merged["flow"]
        + f["resilience_score"] * merged["resilience"]
        + f["regime_fit_score"] * merged["regime_fit"]
        + f["quality_score"] * merged["quality"]
        - f["risk_score"] * 0.12
    ).clip(0, 100).round(1)

    f["data_imputed_secondary"] = imputed
    f["regime"] = regime
    f["regime_confidence"] = confidence
    f["model_version"] = state.get("validation", {}).get("active_model_version", "champion-1")

    # Entry is intentionally not gated by current positive daily return.
    # The only hard gates are data quality and minimum liquidity; final ranking is score based.
    guards = state.get("risk_guards", {})
    f["eligible"] = (
        (f["quality_score"] >= float(guards.get("min_data_quality", 70.0)))
        & (f["value_traded"] >= float(guards.get("min_liquidity_tl", 8_000_000.0)))
        & (f["overnight_risk"] <= float(guards.get("max_overnight_risk", 65.0)))
        & (f["meta_score"] >= float(guards.get("min_signal_score", 72.0)))
    )
    f["selection_reason"] = np.select(
        [
            (f["meta_score"] >= 85) & (f["risk_score"] <= 35),
            (f["meta_score"] >= 78) & (f["risk_score"] <= 45),
            f["meta_score"] >= 72,
        ],
        ["HIGH_CONVICTION", "WATCHLIST_PRIORITY", "QUALIFIED"],
        default="NO_SIGNAL",
    )

    return f.sort_values(["eligible", "meta_score", "pre_move_score"], ascending=[False, False, False]).reset_index(drop=True)
