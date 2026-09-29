"""Scoring and monthly portfolio construction (live AND backtest use these functions).

Rules (long-horizon, low turnover):
* BUY  : composite percentile >= calibrated cut-off, expected 12m REAL return
         after costs >= MIN_EXPECTED_REAL_PCT, liquid, no fundamental break,
         max MAX_PER_SECTOR names per sector, up to TARGET_POSITIONS x exposure.
* HOLD : keep while percentile >= HOLD_PCT and no fundamental break
         (hysteresis 85 -> 60 keeps winners for years).
* SELL : percentile < HOLD_PCT (thesis weakened) or fundamental break;
         catastrophe stop is handled daily in portfolio.apply_day.
* Weights: inverse volatility, capped at MAX_POSITION_W, total = exposure
  (autonomy guard x regime). Idle weight stays in cash. Holdings are only
  re-weighted when they drift more than REBALANCE_BAND.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import config as C
from calibration import bucket_lookup, market_expectation
from factors import build_frame, composite, fund_break
from portfolio import weights as pf_weights


def active_weights(state: Dict) -> Tuple[Dict[str, float], str]:
    model = state.get("model", {})
    champ = model.get("champion_weights") or dict(C.PRIOR_IC)
    reg = state.get("regime", {})
    label = reg.get("label")
    p = float((reg.get("probs") or {}).get(label, 0.0) or 0.0)
    rw = (model.get("regime_weights") or {}).get(label)
    if rw and p >= 0.6 and not reg.get("degraded", True):
        w = {k: p * rw.get(k, 0.0) + (1 - p) * champ.get(k, 0.0) for k in C.FACTORS}
        version = f"{model.get('champion_version', 'prior')}+{label}"
    else:
        w, version = {k: champ.get(k, 0.0) for k in C.FACTORS}, model.get("champion_version", "prior")
    tot = sum(abs(v) for v in w.values()) or 1.0
    return {k: v / tot for k, v in w.items()}, version


def score_universe(price_f: pd.DataFrame, fund_inputs: Optional[pd.DataFrame], state: Dict,
                   cpi_stats: Dict, sector_map: Optional[Dict[str, str]] = None) -> Tuple[pd.DataFrame, Dict]:
    frame, cov = build_frame(price_f, fund_inputs, cpi_stats.get("yoy_pct"), sector_map)
    if frame.empty:
        return frame, {"error": "no_price_factors"}
    w, version = active_weights(state)
    frame["composite"] = composite(frame, w)
    frame["composite_pct"] = frame["composite"].rank(pct=True) * 100.0
    frame["model_version"] = version
    cal = state.get("calibration", {})
    reg = state.get("regime", {})
    calibrated = cal.get("status") == "CALIBRATED"
    table = cal.get("bucket_table", [])
    exc = bucket_lookup(frame["composite_pct"], table, "mean_excess") if calibrated else np.zeros(len(frame))
    frame["exp_excess_12m"] = np.nan_to_num(exc, nan=0.0)
    frame["p_beat_cpi"] = bucket_lookup(frame["composite_pct"], table, "p_beat_cpi") if calibrated else np.nan
    mk = market_expectation(cal, reg.get("label"), reg.get("exp_mkt_12m_pct"))
    infl = cpi_stats.get("expected_12m_pct")
    frame["exp_real_12m"] = np.nan
    frame["exp_nominal_12m"] = np.nan
    if infl is not None and mk["mkt_12m_pct"] is not None:
        if mk.get("basis") == "real":
            # E[real] = E[universe real | regime] + E[excess | score] - costs
            real = mk["mkt_12m_pct"] + frame["exp_excess_12m"]
            frame["exp_nominal_12m"] = ((1 + real / 100.0) * (1 + infl / 100.0) - 1.0) * 100.0 - C.COST_ROUND_TRIP_PCT
        else:
            frame["exp_nominal_12m"] = mk["mkt_12m_pct"] + frame["exp_excess_12m"] - C.COST_ROUND_TRIP_PCT
        frame["exp_real_12m"] = ((1 + frame["exp_nominal_12m"] / 100.0) / (1 + infl / 100.0) - 1.0) * 100.0
    # multi-benchmark hurdle (CPI, USD, gold, deposit): expected margin over the highest one
    hz = state.get("hurdles", {}) or {}
    hurdle = hz.get("hurdle")
    frame["hurdle_12m"] = hurdle if hurdle is not None else np.nan
    frame["exp_over_hurdle"] = frame["exp_nominal_12m"] - hurdle if hurdle is not None else np.nan
    frame["p_beat_all"] = bucket_lookup(frame["composite_pct"], table, "p_beat_all") if calibrated else np.nan
    frame["fund_break"] = fund_break(frame)
    frame["regime_label"] = reg.get("label", "UNKNOWN")
    info = {"weights": {k: round(v, 4) for k, v in w.items()}, "model_version": version,
            "coverage": {k: round(v, 3) for k, v in cov.items()}, "calibrated": calibrated,
            "market_12m": mk, "expected_inflation_12m": infl, "n_scored": int(len(frame)),
            "hurdles": hz,
            "gate": ("multi_hurdle" if hurdle is not None else "real_return") if calibrated and infl is not None else ("rank_only_uncalibrated" if infl is not None else "blocked_no_inflation")}
    return frame, info


def plan_rebalance(pf: Dict, frame: pd.DataFrame, state: Dict, exposure: float,
                   block: bool = False, today_change: Optional[pd.Series] = None) -> Tuple[List[Dict], Dict]:
    """Returns orders for the next open + a summary. Never forces sells because of low exposure."""
    cal = state.get("calibration", {})
    cutoff = float(cal.get("pct_cutoff", C.DEFAULT_PCT_CUTOFF))
    f = frame.set_index("ticker")
    for c in ("exp_over_hurdle", "exp_real_12m", "exp_nominal_12m", "p_beat_all", "hurdle_12m"):
        if c not in f.columns:
            f[c] = np.nan
    cur = list(pf["positions"].keys())
    orders, holds, sells = [], [], []
    if block:
        return [], {"blocked": True, "holds": cur, "sells": [], "buys": []}
    for t in cur:
        if t not in f.index:
            holds.append(t)                     # no data this month: do not trade blind
            continue
        r = f.loc[t]
        flagged = bool(pf["positions"][t].get("dd_flag", False))
        if bool(r.get("fund_break", False)):
            sells.append((t, "FUND_BREAK"))
        elif float(r["composite_pct"]) < C.HOLD_PCT:
            sells.append((t, "RANK_EXIT"))
        elif flagged and float(r["composite_pct"]) < cutoff:
            sells.append((t, "DRAWDOWN_CONFIRMED"))   # deep drawdown AND the thesis has weakened
        else:
            if flagged:
                pf["positions"][t]["dd_flag"] = False  # thesis intact: keep holding through the drawdown
            holds.append(t)
    n_target = C.TARGET_POSITIONS if exposure > 0 else 0
    sector_count: Dict[str, int] = {}
    for t in holds:
        s = str(f.loc[t, "sector"]) if t in f.index and pd.notna(f.loc[t].get("sector")) else "NA"
        sector_count[s] = sector_count.get(s, 0) + 1
    elig = (
        (f["composite_pct"] >= cutoff)
        & (pd.to_numeric(f["med_value_traded"], errors="coerce") >= C.MIN_MEDIAN_VALUE_TRADED_TL)
        & ~f["fund_break"].astype(bool)
        & ~f.index.isin(cur)
    )
    calibrated = cal.get("status") == "CALIBRATED"
    has_real = f["exp_real_12m"].notna().any()
    no_inflation = bool(state.get("_no_inflation", False))
    if no_inflation:
        elig &= False          # inflation unknown -> cannot judge "beats CPI" -> no new buys
    elif calibrated and f["exp_over_hurdle"].notna().any():
        # must be expected to beat CPI, USD (+US inflation), gold and TL deposit by a margin
        elig &= f["exp_over_hurdle"] >= C.MIN_EDGE_OVER_HURDLE_PCT
    elif calibrated and has_real:
        elig &= f["exp_real_12m"] >= C.MIN_EXPECTED_REAL_PCT
    # else: CPI known but no calibration yet (early backtest years) -> rank-only selection
    if today_change is not None:
        chg = today_change.reindex(f.index)
        elig &= ~(chg >= C.LIMIT_MOVE_PCT)
    cand = f[elig].copy()
    sort_col = "exp_over_hurdle" if cand["exp_over_hurdle"].notna().any() else (
        "exp_real_12m" if cand["exp_real_12m"].notna().any() else "composite")
    cand = cand.sort_values([sort_col, "composite"], ascending=False)
    buys = []
    for t, r in cand.iterrows():
        if len(holds) + len(buys) >= n_target:
            break
        s = str(r.get("sector")) if pd.notna(r.get("sector")) else "NA"
        if s != "NA" and sector_count.get(s, 0) >= C.MAX_PER_SECTOR:
            continue
        sector_count[s] = sector_count.get(s, 0) + 1
        buys.append(t)
    final = holds + buys
    tw = {}
    if final:
        vol = pd.to_numeric(f["vol_ann_pct"], errors="coerce")
        med = float(vol.median()) if vol.notna().any() else 40.0
        inv = {t: 1.0 / max(float(vol.get(t, med)) if pd.notna(vol.get(t, np.nan)) else med, 5.0) for t in final}
        tot = sum(inv.values())
        total_exp = min(1.0, max(0.0, exposure)) if n_target > 0 else 0.0
        raw = {t: v / tot * total_exp for t, v in inv.items()}
        for _ in range(10):                                   # cap and redistribute
            over = {t: w for t, w in raw.items() if w > C.MAX_POSITION_W}
            if not over:
                break
            excess = sum(w - C.MAX_POSITION_W for w in over.values())
            for t in over:
                raw[t] = C.MAX_POSITION_W
            under = [t for t in raw if raw[t] < C.MAX_POSITION_W]
            us = sum(inv[t] for t in under)
            if not under or us <= 0:
                break
            for t in under:
                raw[t] += excess * inv[t] / us
        tw = raw
    cw = pf_weights(pf)
    for t, why in sells:
        orders.append({"ticker": t, "action": "SELL", "reason": why, "target_w": 0.0})
    for t in buys:
        r = f.loc[t]
        orders.append({"ticker": t, "action": "BUY", "reason": "NEW_ENTRY", "target_w": round(tw.get(t, 0.0), 5),
                       "entry_pct": round(float(r["composite_pct"]), 2),
                       "entry_exp_real": None if pd.isna(r["exp_real_12m"]) else round(float(r["exp_real_12m"]), 2),
                       "entry_exp_nominal": None if pd.isna(r["exp_nominal_12m"]) else round(float(r["exp_nominal_12m"]), 2),
                       "entry_hurdle": None if pd.isna(r["hurdle_12m"]) else round(float(r["hurdle_12m"]), 2),
                       "entry_p_beat_all": None if pd.isna(r["p_beat_all"]) else round(float(r["p_beat_all"]), 3)})
    for t in holds:
        if t in tw and abs(cw.get(t, 0.0) - tw[t]) > C.REBALANCE_BAND:
            # only trim overweights when exposure is reduced; never add to a position in a blocked state
            orders.append({"ticker": t, "action": "REBAL", "reason": "WEIGHT_DRIFT", "target_w": round(tw[t], 5)})
    summary = {"cutoff": cutoff, "n_target": n_target, "no_inflation_block": no_inflation, "holds": holds, "sells": [s for s, _ in sells], "sell_reasons": {t: w for t, w in sells},
               "buys": buys, "target_weights": {k: round(v, 4) for k, v in tw.items()},
               "cash_target": round(1.0 - sum(tw.values()), 4)}
    return orders, summary


def exposure_from(guard: Dict, regime: Dict) -> float:
    """Long-horizon exposure: fully invested by default; guard mode and regime only trim."""
    mode = str(guard.get("mode", "NORMAL")).upper()
    if guard.get("block_new_entries"):
        return 0.0
    base = C.EXPOSURE_BY_MODE.get(mode, 0.9)
    p_off = regime.get("p_risk_off")
    p_off = 0.5 if p_off is None else float(p_off)
    regime_mult = float(np.clip(1.0 - 0.25 * p_off, C.REGIME_EXPOSURE_FLOOR, 1.0))
    return float(base * regime_mult)
