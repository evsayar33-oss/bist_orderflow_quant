"""Data-integrity and source-quality guardrails for Adaptive BIST Orderflow Meta-Engine V1.

Production rule: missing external data is never replaced with fabricated market facts.
Rows/fields that cannot be trusted are marked with explicit confidence and may be blocked.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, Tuple

import numpy as np
import pandas as pd

REQUIRED_MARKET = [
    "ticker", "close", "open", "high", "low", "volume", "change_%",
    "value_traded", "rvol", "perf_w", "perf_1m", "perf_y"
]
RECOMMENDED_MARKET = [
    "high_1m", "low_1m", "roe", "pb", "pe", "oper_margin", "market_cap",
    "foreign_ratio", "foreign_ratio_source", "foreign_ratio_confidence"
]


def _finite(x) -> bool:
    try:
        return np.isfinite(float(x))
    except Exception:
        return False


def _safe_num(v, default=np.nan) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def validate_market_frame(df: pd.DataFrame, min_rows: int = 30) -> Tuple[pd.DataFrame, Dict]:
    """Validate and normalize a current market snapshot without fabricating observations."""
    if df is None or df.empty:
        return pd.DataFrame(), {"ok": False, "reason": "empty_market_frame", "score": 0.0}

    out = df.copy()
    missing = [c for c in REQUIRED_MARKET if c not in out.columns]
    if missing:
        return pd.DataFrame(), {"ok": False, "reason": f"missing_required:{','.join(missing)}", "score": 0.0}

    for col in REQUIRED_MARKET + RECOMMENDED_MARKET:
        if col in out.columns and col not in ("ticker", "foreign_ratio_source"):
            out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.drop_duplicates(subset=["ticker"], keep="last").copy()

    numeric_required = ["close", "open", "high", "low", "volume", "change_%", "value_traded", "rvol"]
    invalid_mask = np.zeros(len(out), dtype=bool)
    for col in numeric_required:
        invalid_mask |= ~out[col].map(_finite).to_numpy()

    o = out["open"].to_numpy(dtype=float)
    h = out["high"].to_numpy(dtype=float)
    l = out["low"].to_numpy(dtype=float)
    c = out["close"].to_numpy(dtype=float)
    invalid_ohlc = (o <= 0) | (h <= 0) | (l <= 0) | (c <= 0) | (h < np.maximum(o, c)) | (l > np.minimum(o, c)) | (h < l)
    invalid_mask |= invalid_ohlc
    invalid_value = out["value_traded"].to_numpy(dtype=float) < 0
    invalid_rvol = out["rvol"].to_numpy(dtype=float) < 0
    invalid_mask |= invalid_value | invalid_rvol

    invalid_rows = int(invalid_mask.sum())
    if invalid_rows:
        out = out.loc[~invalid_mask].copy()

    if out.empty:
        return pd.DataFrame(), {"ok": False, "reason": "all_rows_invalid", "score": 0.0, "invalid_rows": invalid_rows}

    quality_penalty = 0.0
    if len(out) < min_rows:
        quality_penalty += 25.0

    missing_optional = [c for c in RECOMMENDED_MARKET if c not in out.columns]
    quality_penalty += min(20.0, len(missing_optional) * 2.0)

    missing_rate = float(out[REQUIRED_MARKET].isna().mean().mean()) if REQUIRED_MARKET else 0.0
    quality_penalty += min(30.0, missing_rate * 100.0)

    duplicate_rate = invalid_rows / max(len(out) + invalid_rows, 1)
    quality_penalty += min(10.0, duplicate_rate * 50.0)

    score = round(max(0.0, 100.0 - quality_penalty), 1)
    ok = len(out) >= max(5, min_rows // 3) and score >= 70.0
    summary = {
        "ok": ok,
        "score": score,
        "rows": int(len(out)),
        "invalid_rows": invalid_rows,
        "missing_optional": missing_optional,
        "missing_rate": round(missing_rate, 4),
    }

    return out.reset_index(drop=True), summary


def apply_explicit_imputation(df: pd.DataFrame, columns: Iterable[str]) -> Tuple[pd.DataFrame, pd.Series]:
    """Median-impute selected secondary analytics only; return a per-row imputation flag."""
    out = df.copy()
    flag = pd.Series(False, index=out.index)
    for col in columns:
        if col not in out.columns:
            continue
        s = pd.to_numeric(out[col], errors="coerce")
        if s.notna().any():
            med = float(s.median())
            miss = s.isna()
            out[col] = s.fillna(med)
            flag = flag | miss
    return out, flag


def data_quality_score(df: pd.DataFrame) -> pd.Series:
    """Row-level quality score. Missing critical source facts reduce confidence, never create facts."""
    score = pd.Series(100.0, index=df.index)
    critical = ["close", "open", "high", "low", "volume", "value_traded", "rvol"]
    for col in critical:
        if col not in df.columns:
            score -= 12.0
        else:
            score -= df[col].isna().astype(float) * 12.0

    for col in ["roe", "pb", "pe", "oper_margin", "high_1m", "low_1m"]:
        if col not in df.columns:
            score -= 3.0
        else:
            score -= df[col].isna().astype(float) * 3.0

    if "foreign_ratio_confidence" in df.columns:
        conf = pd.to_numeric(df["foreign_ratio_confidence"], errors="coerce").fillna(0.0).clip(0, 100)
        score = score * 0.85 + conf * 0.15

    return score.clip(0, 100).round(1)


def require_real_history(df: pd.DataFrame, min_rows: int = 250) -> None:
    """Fail-closed guard for production/historical backtests."""
    if df is None or df.empty:
        raise RuntimeError("Gerçek tarihsel veri bulunamadı; backtest durduruldu.")
    if "tarih" not in df.columns:
        raise RuntimeError("Tarih kolonu olmayan veri ile backtest yapılamaz.")
    dates = pd.to_datetime(df["tarih"], errors="coerce").dropna().dt.normalize().nunique()
    if dates < min_rows:
        raise RuntimeError(f"Gerçek tarihsel veri yetersiz: {dates} işlem günü < {min_rows}. Sentetik veri üretimi kapalı.")
