"""Factor engineering shared by the live engine AND the backtest (single code path).

Design principles (fixes of V1):
* Features are computed from a full end-of-day bar (no time-of-day bias).
* Every factor is a distinct primitive; no factor is a linear combination of
  others (V1's regime_fit was). Remaining collinearity is handled by the
  learner through the factor-correlation matrix (Grinold-Kahn weighting).
* Cross-sectional transform: rank -> Gaussian -> sector-neutral -> z-score,
  winsorised at +-3. Missing values are neutral (0) and coverage is reported.
* Proxies are called proxies. Real custody flow ("takas_delta") is optional.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd
from scipy.stats import norm

import config as C


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype=float)


def raw_factors(snap: pd.DataFrame, prev_takas: Optional[pd.Series] = None) -> pd.DataFrame:
    """Raw (un-normalised) factor values from one EOD snapshot."""
    s = snap
    f = pd.DataFrame(index=s.index)
    close = _col(s, "close")
    high = _col(s, "high")
    low = _col(s, "low")
    rvol = _col(s, "rvol")
    span = (high - low).where(lambda x: x > 0)

    f["mom_3m"] = _col(s, "perf_3m")
    f["rs_1m"] = _col(s, "perf_1m")
    hi1, lo1 = _col(s, "high_1m"), _col(s, "low_1m")
    rng = (hi1 - lo1).where(lambda x: x > 0)
    f["range_pos"] = ((close - lo1) / rng).clip(0, 1)
    f["rev_1d"] = -_col(s, "change_pct")
    clv = (((close - low) - (high - close)) / span).clip(-1, 1)
    f["flow_clv"] = clv * rvol.clip(lower=0, upper=5)
    f["vol_surge"] = np.log(rvol.clip(lower=0.05, upper=20))
    atr_pct = _col(s, "atr") / close * 100.0
    f["low_vol"] = -atr_pct
    f["liquidity"] = np.log1p(_col(s, "value_traded").clip(lower=0))
    tk = _col(s, "takas_conc")
    if prev_takas is not None and tk.notna().any():
        prev = s["ticker"].map(prev_takas)
        f["takas_delta"] = tk - pd.to_numeric(prev, errors="coerce")
    else:
        f["takas_delta"] = np.nan
    return f.replace([np.inf, -np.inf], np.nan)


def _gauss_rank(x: pd.Series) -> pd.Series:
    n = x.notna().sum()
    if n < 5:
        return pd.Series(np.nan, index=x.index)
    r = x.rank(method="average")
    return pd.Series(norm.ppf((r - 0.5) / n), index=x.index)


def cross_sectional_z(raw: pd.DataFrame, sector: Optional[pd.Series] = None) -> (pd.DataFrame, Dict[str, float]):
    z = pd.DataFrame(index=raw.index)
    coverage = {}
    for k in C.FACTORS:
        x = raw[k] if k in raw else pd.Series(np.nan, index=raw.index)
        coverage[k] = float(x.notna().mean())
        if coverage[k] < 0.5:
            z[k] = 0.0
            continue
        g = _gauss_rank(x)
        if sector is not None:
            sec = sector.fillna("NA").astype(str)
            sizes = sec.map(sec.value_counts())
            means = g.groupby(sec).transform("mean")
            g = g - means.where(sizes >= 5, 0.0)
        sd = g.std(ddof=0)
        g = (g - g.mean()) / sd if sd and np.isfinite(sd) and sd > 0 else g * 0.0
        z[k] = g.clip(-3, 3).fillna(0.0)
    return z, coverage


def build_factor_frame(snap: pd.DataFrame, prev_takas: Optional[pd.Series] = None) -> (pd.DataFrame, Dict[str, float]):
    """Returns snapshot + raw factor columns (f_*) + z columns (z_*) and coverage."""
    raw = raw_factors(snap, prev_takas)
    sector = snap["sector"] if "sector" in snap.columns and snap["sector"].notna().mean() > 0.5 else None
    z, cov = cross_sectional_z(raw, sector)
    out = snap.copy()
    for k in C.FACTORS:
        out[f"f_{k}"] = raw[k]
        out[f"z_{k}"] = z[k]
    out["atr_pct"] = _col(snap, "atr") / _col(snap, "close") * 100.0
    return out, cov


def composite(frame: pd.DataFrame, weights: Dict[str, float]) -> pd.Series:
    s = pd.Series(0.0, index=frame.index)
    for k, w in weights.items():
        col = f"z_{k}"
        if col in frame:
            s = s + float(w) * frame[col].fillna(0.0)
    return s


def z_matrix(frame: pd.DataFrame) -> np.ndarray:
    return np.column_stack([frame[f"z_{k}"].fillna(0.0).to_numpy(dtype=float) for k in C.FACTORS])
