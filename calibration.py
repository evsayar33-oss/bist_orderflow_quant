"""Score -> expected 12-month REAL return and probability of beating CPI.

  E[real 12m] = (1 + E[market 12m nominal | regime] + E[excess | score bucket])
                / (1 + expected 12m CPI) - 1 - costs
  P(beat CPI | bucket) = share of names in that bucket whose 12m real return > 0

Excess returns are measured against the same-date universe mean (inflation and
market cancel out), so buckets are comparable across very different inflation
eras. Standard errors: date-clustered Newey-West with lag = horizon-1 because
monthly 12-month labels overlap. The composite used here is the one RECORDED
at decision time (live) or out-of-sample (backtest) -> no in-sample optimism.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import config as C
from learner_engine import newey_west

BUCKETS = [(0.0, 50.0), (50.0, 70.0), (70.0, 80.0), (80.0, 90.0), (90.0, 95.0), (95.0, 100.01)]
PRIOR_SE_INFLATION = 1.5
Z_LCB = 1.2816


def add_excess(df: pd.DataFrame) -> pd.DataFrame:
    d = df.dropna(subset=["fwd_ret", "composite_pct"]).copy()
    d["excess"] = d["fwd_ret"] - d.groupby("tarih")["fwd_ret"].transform("mean")
    return d


def _clustered(d: pd.DataFrame) -> Dict:
    per = d.groupby("tarih")["excess"].mean()
    m, se, t, n = newey_west(per, C.LABEL_HORIZON - 1)
    hit = d["real_ret"].dropna() if "real_ret" in d else pd.Series(dtype=float)
    return {"mean": float(m), "se": float(se) if np.isfinite(se) else 99.0, "n_dates": int(n), "n": int(len(d)),
            "p_beat_cpi": round(float((hit > 0).mean()), 4) if len(hit) else None,
            "n_real": int(len(hit))}


def bucket_table(d: pd.DataFrame) -> List[Dict]:
    out = []
    for lo, hi in BUCKETS:
        sub = d[(d["composite_pct"] >= lo) & (d["composite_pct"] < hi)]
        st = _clustered(sub) if len(sub) else {"mean": 0.0, "se": 99.0, "n_dates": 0, "n": 0, "p_beat_cpi": None, "n_real": 0}
        out.append({"lo": lo, "hi": hi, "mean_excess": st["mean"], "se": st["se"], "n_dates": st["n_dates"],
                    "n": st["n"], "p_beat_cpi": st["p_beat_cpi"], "n_real": st["n_real"]})
    return out


def cutoff_stats(d: pd.DataFrame) -> Dict[str, Dict]:
    res = {}
    for c in C.PCT_CUTOFF_CANDIDATES:
        sub = d[d["composite_pct"] >= c]
        res[str(c)] = _clustered(sub) if len(sub) else {"mean": 0.0, "se": 99.0, "n_dates": 0, "n": 0}
    return res


def market_stats(d: pd.DataFrame) -> Dict:
    """Universe-mean 12m nominal return per date, overall and per regime label."""
    per = d.groupby("tarih").agg(mkt=("fwd_ret", "mean"), reg=("regime_label", "first")) if "regime_label" in d \
        else d.groupby("tarih").agg(mkt=("fwd_ret", "mean"))
    m, se, _, n = newey_west(per["mkt"], C.LABEL_HORIZON - 1)
    out = {"all": {"mean": float(m), "se": float(se) if np.isfinite(se) else 99.0, "n_dates": int(n)}}
    if "reg" in per:
        for r, g in per.dropna(subset=["reg"]).groupby("reg"):
            mm, ss, _, nn = newey_west(g["mkt"], C.LABEL_HORIZON - 1)
            out[str(r)] = {"mean": float(mm), "se": float(ss) if np.isfinite(ss) else 99.0, "n_dates": int(nn)}
    return out


def _combine(live: Optional[Dict], prior: Optional[Dict]) -> Dict:
    parts = []
    for x, infl in ((live, 1.0), (prior, PRIOR_SE_INFLATION)):
        if x and x.get("n_dates", 0) >= 12 and np.isfinite(x.get("se", np.inf)) and x["se"] < 50:
            parts.append((x["mean"], x["se"] * infl))
    if not parts:
        return {"mean": 0.0, "se": 99.0}
    prec = np.array([1.0 / max(s, 1e-6) ** 2 for _, s in parts])
    m = float((prec * np.array([v for v, _ in parts])).sum() / prec.sum())
    return {"mean": m, "se": float(np.sqrt(1.0 / prec.sum()))}


def calibrate(state: Dict, dataset: Optional[pd.DataFrame], research: Optional[Dict]) -> Dict:
    cal = state.setdefault("calibration", {})
    prior = (research or {}).get("calibration", {}) or {}
    live_b, live_c, live_m = [], {}, {}
    if dataset is not None and not dataset.empty and {"composite_pct", "fwd_ret"} <= set(dataset.columns):
        d = add_excess(dataset)
        if not d.empty and d["tarih"].nunique() >= 12:
            live_b, live_c, live_m = bucket_table(d), cutoff_stats(d), market_stats(d)
    pb = {(b["lo"], b["hi"]): b for b in prior.get("buckets", [])}
    table = []
    for i, (lo, hi) in enumerate(BUCKETS):
        lb = live_b[i] if live_b else None
        pr = pb.get((lo, hi))
        post = _combine({"mean": lb["mean_excess"], "se": lb["se"], "n_dates": lb["n_dates"]} if lb else None,
                        {"mean": pr["mean_excess"], "se": pr["se"], "n_dates": pr["n_dates"]} if pr else None)
        pbeat = None
        num = den = 0.0
        for src in (lb, pr):
            if src and src.get("p_beat_cpi") is not None and src.get("n_real", 0) > 0:
                num += src["p_beat_cpi"] * src["n_real"]
                den += src["n_real"]
        if den:
            pbeat = round(num / den, 4)
        table.append({"lo": lo, "hi": hi, "mean_excess": round(post["mean"], 3), "se": round(post["se"], 3),
                      "p_beat_cpi": pbeat, "live_n_dates": lb["n_dates"] if lb else 0})
    best, best_lcb, rep, evidence = C.DEFAULT_PCT_CUTOFF, -np.inf, {}, False
    for c in C.PCT_CUTOFF_CANDIDATES:
        post = _combine(live_c.get(str(c)), prior.get("cutoffs", {}).get(str(c)))
        lcb = post["mean"] - Z_LCB * post["se"]
        rep[str(c)] = {"mean_excess": round(post["mean"], 3), "se": round(post["se"], 3), "lcb": round(lcb, 3)}
        if post["se"] < 50:
            evidence = True
            if lcb > best_lcb:
                best, best_lcb = c, lcb
    mk = {}
    pm = prior.get("market", {}) or {}
    for key in set(pm) | set(live_m):
        mk[key] = _combine(live_m.get(key), pm.get(key))
    cal.update({"pct_cutoff": float(best if evidence else C.DEFAULT_PCT_CUTOFF), "bucket_table": table,
                "cutoffs": rep, "market": {k: {"mean": round(v["mean"], 3), "se": round(v["se"], 3)} for k, v in mk.items()},
                "status": "CALIBRATED" if evidence else "DEFAULT_CUTOFF",
                "source": "live+research" if live_c and prior else "live" if live_c else "research" if prior else "default"})
    return state


def bucket_lookup(pct, table: List[Dict], key: str) -> np.ndarray:
    pct = np.asarray(pct, float)
    out = np.full(pct.shape, np.nan)
    for b in table or []:
        m = (pct >= b["lo"]) & (pct < b["hi"])
        v = b.get(key)
        out[m] = np.nan if v is None else float(v)
    return out


def market_expectation(cal: Dict, regime_label: Optional[str], hmm_12m_pct: Optional[float]) -> Dict:
    """Expected 12m nominal universe return: calibrated by regime when available."""
    mk = cal.get("market", {}) or {}
    for key, src in ((regime_label, "calibrated_regime"), ("all", "calibrated_all")):
        if key and key in mk and mk[key]["se"] < 50:
            return {"mkt_12m_pct": float(mk[key]["mean"]), "source": src}
    if hmm_12m_pct is not None:
        return {"mkt_12m_pct": float(hmm_12m_pct), "source": "hmm"}
    return {"mkt_12m_pct": None, "source": "none"}
