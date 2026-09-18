"""Feature engineering for Adaptive BIST Orderflow Meta-Engine V1."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _rank(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    if s.notna().sum() <= 1:
        return pd.Series(50.0, index=s.index)
    return (s.rank(pct=True, method="average").fillna(0.5) * 100.0).clip(0, 100)


def build_flow_features(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()

    # Structure / location, not directional momentum.
    span_1m = (out["high_1m"] - out["low_1m"]).replace(0, np.nan)
    out["range_position"] = (((out["close"] - out["low_1m"]) / span_1m) * 100.0).replace([np.inf, -np.inf], np.nan).fillna(50.0).clip(0, 100)
    out["dist_from_support"] = (((out["close"] - out["low_1m"]) / out["low_1m"]) * 100.0).replace([np.inf, -np.inf], np.nan)

    span = (out["high"] - out["low"]).replace(0, np.nan)
    out["clv"] = (((out["close"] - out["low"]) - (out["high"] - out["close"])) / span).replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-1, 1)
    out["body_efficiency"] = ((out["close"] - out["open"]) / span).replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-1, 1)

    # Flow proxies are labeled as proxies; real Takas data remains separate.
    out["flow_proxy"] = (out["clv"].clip(lower=0) * 0.55 + out["body_efficiency"].clip(lower=0) * 0.25 + ((out["rvol"] - 1.0).clip(lower=0) / 3.0) * 0.20).clip(0, 1)
    out["absorption_proxy"] = ((1.0 - out["range_position"] / 100.0).clip(0, 1) * out["rvol"].clip(lower=0).clip(upper=3.0) / 3.0).clip(0, 1)
    out["activity_anomaly"] = ((out["rvol"] - 1.0) / 2.0).clip(-1, 1)

    # Favor base/accumulation zone but do not require positive daily price change.
    out["accumulation_score"] = np.select(
        [
            out["range_position"].between(5, 45) & (out["dist_from_support"] <= 10),
            out["range_position"].between(0, 60) & (out["dist_from_support"] <= 18),
            out["range_position"] >= 85,
        ],
        [85.0, 65.0, 15.0],
        default=40.0,
    )

    out["volume_ignition"] = (out["rvol"].clip(lower=0) * 35.0 + out["activity_anomaly"].clip(lower=0) * 25.0).clip(0, 100)
    out["takas_quality"] = pd.to_numeric(out.get("foreign_ratio_confidence", 0.0), errors="coerce").fillna(0.0).clip(0, 100)
    out["takas_flow"] = _rank(pd.to_numeric(out.get("foreign_ratio", np.nan), errors="coerce"))

    out["pct_accum"] = _rank(out["accumulation_score"])
    out["pct_flow_proxy"] = _rank(out["flow_proxy"])
    out["pct_absorption"] = _rank(out["absorption_proxy"])
    out["pct_volume"] = _rank(out["volume_ignition"])
    out["pct_takas"] = _rank(out["takas_flow"])
    out["pct_liquidity"] = _rank(np.log1p(out["value_traded"].clip(lower=0)))

    out["pre_move_score"] = (
        out["pct_accum"] * 0.30
        + out["pct_flow_proxy"] * 0.20
        + out["pct_absorption"] * 0.20
        + out["pct_volume"] * 0.15
        + out["pct_takas"] * 0.10
        + out["pct_liquidity"] * 0.05
    ).clip(0, 100).round(1)

    out["flow_score"] = (
        out["pct_flow_proxy"] * 0.35
        + out["pct_absorption"] * 0.25
        + out["pct_volume"] * 0.20
        + out["pct_takas"] * 0.15
        + out["pct_liquidity"] * 0.05
    ).clip(0, 100).round(1)

    out["structural_risk"] = np.select(
        [
            out["range_position"] >= 85,
            out["dist_from_support"] > 30,
            out["rvol"] < 0.60,
        ],
        [80.0, 65.0, 70.0],
        default=25.0,
    )

    return out
