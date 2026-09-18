"""Walk-forward/OOS validation and robustness gates."""
from __future__ import annotations

from typing import Dict
import numpy as np
import pandas as pd


def performance_metrics(trades: pd.DataFrame) -> Dict[str, float]:
    if trades is None or trades.empty:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "avg_pnl": 0.0, "mdd": 0.0}
    pnl = pd.to_numeric(trades["pnl_pct"], errors="coerce").fillna(0.0)
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    eq = np.cumprod(1 + pnl.to_numpy() / 100.0)
    peak = np.maximum.accumulate(eq)
    mdd = float(((eq / peak) - 1).min() * 100.0)
    pf = float(wins.sum() / abs(losses.sum())) if len(losses) else float("inf")
    return {
        "trades": int(len(pnl)),
        "win_rate": round(float((pnl > 0).mean() * 100), 2),
        "profit_factor": round(pf, 3) if np.isfinite(pf) else 999.0,
        "avg_pnl": round(float(pnl.mean()), 3),
        "mdd": round(mdd, 3),
    }


def validate_candidate(trades: pd.DataFrame, min_trades: int = 30) -> Dict:
    m = performance_metrics(trades)
    reasons = []
    if m["trades"] < min_trades:
        reasons.append("INSUFFICIENT_SAMPLE")
    if m["profit_factor"] < 1.05:
        reasons.append("PF_TOO_LOW")
    if m["avg_pnl"] <= 0:
        reasons.append("NEGATIVE_EXPECTANCY")
    m["passed"] = len(reasons) == 0
    m["reasons"] = reasons
    return m


def stability_check(metrics_list) -> Dict:
    if not metrics_list:
        return {"stable": False, "dispersion": 1.0}
    pfs = [float(m.get("profit_factor", 0.0)) for m in metrics_list if m.get("trades", 0) > 0]
    wrs = [float(m.get("win_rate", 0.0)) for m in metrics_list if m.get("trades", 0) > 0]
    if not pfs:
        return {"stable": False, "dispersion": 1.0}
    pf_disp = float(np.std(pfs) / (abs(np.mean(pfs)) + 1e-9))
    wr_disp = float(np.std(wrs) / (abs(np.mean(wrs)) + 1e-9))
    return {"stable": pf_disp < 0.35 and wr_disp < 0.25, "pf_dispersion": round(pf_disp, 4), "wr_dispersion": round(wr_disp, 4)}
