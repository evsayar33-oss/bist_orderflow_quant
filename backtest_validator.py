"""Trade- and portfolio-level performance metrics (date-ordered, cost-aware)."""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from learner_engine import newey_west


def trade_metrics(trades: pd.DataFrame) -> Dict:
    if trades is None or trades.empty:
        return {"trades": 0}
    r = pd.to_numeric(trades["net_ret_pct"], errors="coerce").dropna()
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    per_date = trades.assign(r=r).groupby("signal_date")["r"].mean()
    m, se, t, n = newey_west(per_date, 5)
    return {
        "trades": int(len(r)),
        "hit_rate_pct": round(float((r > 0).mean() * 100), 2),
        "avg_net_pct": round(float(r.mean()), 4),
        "median_net_pct": round(float(r.median()), 4),
        "profit_factor": round(float(gains / losses), 3) if losses > 0 else None,
        "date_clustered_mean": round(m, 4),
        "date_clustered_t": round(t, 3),
        "date_clustered_lcb90": round(m - 1.2816 * se, 4) if np.isfinite(se) else None,
        "exit_mix": trades["exit_reason"].value_counts().to_dict() if "exit_reason" in trades else {},
    }


def portfolio_metrics(daily_ret: pd.Series) -> Dict:
    r = pd.to_numeric(daily_ret, errors="coerce").fillna(0.0)
    if r.empty:
        return {"days": 0}
    eq = (1.0 + r).cumprod()
    peak = eq.cummax()
    mdd = float((eq / peak - 1.0).min() * 100.0)
    years = max(len(r) / 252.0, 1e-9)
    cagr = float(eq.iloc[-1] ** (1.0 / years) - 1.0) * 100.0 if eq.iloc[-1] > 0 else -100.0
    vol = float(r.std(ddof=0) * np.sqrt(252) * 100.0)
    sharpe = float(r.mean() / r.std(ddof=0) * np.sqrt(252)) if r.std(ddof=0) > 0 else 0.0
    return {"days": int(len(r)), "total_return_pct": round(float(eq.iloc[-1] - 1.0) * 100.0, 2),
            "cagr_pct": round(cagr, 2), "ann_vol_pct": round(vol, 2), "sharpe": round(sharpe, 3),
            "max_drawdown_pct": round(mdd, 2), "calmar": round(cagr / abs(mdd), 3) if mdd < 0 else None}


def stability(fold_metrics: List[Dict]) -> Dict:
    vals = [f.get("date_clustered_mean") for f in fold_metrics if f.get("trades", 0) >= 10]
    if len(vals) < 2:
        return {"stable": False, "reason": "too_few_folds", "positive_folds": 0, "folds": len(vals)}
    pos = sum(1 for v in vals if v is not None and v > 0)
    return {"stable": bool(pos / len(vals) >= 0.6), "positive_folds": pos, "folds": len(vals),
            "fold_means": [round(float(v), 4) for v in vals]}
