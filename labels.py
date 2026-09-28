"""Triple-barrier labels and the trade-management rule used EVERYWHERE.

The same `plan_trade` / `barrier_step` functions drive:
* the learner's training labels,
* the calibration of expected trade returns,
* the walk-forward backtest,
* the live position ledger.
So the objective the system optimises is exactly the trade the system takes.

Trade rule: signal at close t -> buy at open t+1 (skipped if the open gaps to the
upper price limit) -> ATR stop, target = RR x stop, break-even after +1R,
time exit at close of session t+MAX_HOLD. Same-bar stop/target ambiguity is
resolved pessimistically (stop first). Gaps through a barrier fill at the open.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

import config as C

EXIT_NONE, EXIT_STOP, EXIT_TARGET, EXIT_TIME = 0, 1, 2, 3
EXIT_NAMES = {EXIT_STOP: "STOP", EXIT_TARGET: "TARGET", EXIT_TIME: "TIME"}


def stop_distance_pct(atr_pct):
    a = np.asarray(atr_pct, dtype=float)
    d = np.clip(C.STOP_ATR_MULT * a, C.STOP_MIN_PCT, C.STOP_MAX_PCT)
    return np.where(np.isfinite(d), d, (C.STOP_MIN_PCT + C.STOP_MAX_PCT) / 2.0)


def plan_trade(entry, stop_dist_pct):
    entry = np.asarray(entry, dtype=float)
    sd = np.asarray(stop_dist_pct, dtype=float) / 100.0
    return {
        "stop": entry * (1.0 - sd),
        "target": entry * (1.0 + C.TARGET_RR * sd),
        "be_trigger": entry * (1.0 + C.BREAKEVEN_TRIGGER_R * sd),
        "be_stop": entry * (1.0 + C.BREAKEVEN_BUFFER_PCT / 100.0),
    }


def entry_blocked(signal_close, next_open):
    """True when the next open is at/near the upper limit (not realistically fillable)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.asarray(next_open, float) >= np.asarray(signal_close, float) * (1.0 + C.LIMIT_MOVE_PCT / 100.0)


def barrier_step(o, h, l, c, stop, target, be_trigger, be_stop, armed, is_entry_day, is_last_day):
    """One session of trade management, vectorised. Returns
    (exit_code, exit_price, new_stop, new_armed). Arrays of equal length.
    NaN bars produce no action (caller decides validity)."""
    o, h, l, c = (np.asarray(x, float) for x in (o, h, l, c))
    n = o.shape[0]
    code = np.zeros(n, dtype=int)
    px = np.full(n, np.nan)
    valid = np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c)
    stop = np.asarray(stop, float).copy()
    armed = np.asarray(armed, bool).copy()
    if not is_entry_day:
        gap_stop = valid & (o <= stop)
        code[gap_stop], px[gap_stop] = EXIT_STOP, o[gap_stop]
        gap_tgt = valid & (code == 0) & (o >= target)
        code[gap_tgt], px[gap_tgt] = EXIT_TARGET, o[gap_tgt]
    hit_stop = valid & (code == 0) & (l <= stop)
    code[hit_stop], px[hit_stop] = EXIT_STOP, stop[hit_stop]
    hit_tgt = valid & (code == 0) & (h >= target)
    code[hit_tgt], px[hit_tgt] = EXIT_TARGET, np.asarray(target, float)[hit_tgt]
    if is_last_day:
        tm = valid & (code == 0)
        code[tm], px[tm] = EXIT_TIME, c[tm]
    arm = valid & (code == 0) & (h >= be_trigger)
    armed = armed | arm
    stop = np.where(armed, np.maximum(stop, be_stop), stop)
    return code, px, stop, armed


def compute_labels(panel: Dict, horizon: int = C.LABEL_HORIZON, max_hold: int = C.MAX_HOLD) -> pd.DataFrame:
    """Long frame: tarih, ticker, fwd_ret, barrier_gross, barrier_net, exit_reason, filled.
    Only labels whose whole path lies on contiguous, break-free sessions are produced."""
    if not panel:
        return pd.DataFrame()
    O, H, L, Cl = (panel[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    ATRP = panel["atr_pct"].to_numpy(dtype=float)
    BR = panel["breaks"].to_numpy(dtype=bool)
    dates = panel["dates"]
    tickers = list(panel["close"].columns)
    T, N = Cl.shape
    adjacent = np.asarray(panel.get("adjacent", np.ones(T, bool)), bool)
    nonadj_cum = np.cumsum(~adjacent)
    br_cum = np.vstack([np.zeros((1, N), int), np.cumsum(BR, axis=0)])  # br_cum[i] = breaks in rows < i

    out = []
    for i in range(T - 1):
        kmax = min(max_hold, T - 1 - i)
        contiguous_k = 0
        for k in range(1, kmax + 1):
            if nonadj_cum[i + k] - nonadj_cum[i] == 0:
                contiguous_k = k
            else:
                break
        if contiguous_k == 0:
            continue
        entry = O[i + 1]
        sig_close = Cl[i]
        fwd = np.full(N, np.nan)
        if contiguous_k >= horizon:
            fwd = Cl[i + horizon] / entry - 1.0
            clean_h = (br_cum[i + horizon + 1] - br_cum[i + 1]) == 0
            fwd = np.where(clean_h, fwd, np.nan)

        blocked = entry_blocked(sig_close, entry) | ~np.isfinite(entry) | ~np.isfinite(sig_close)
        sd = stop_distance_pct(ATRP[i])
        p = plan_trade(entry, sd)
        stop, armed = p["stop"], np.zeros(N, bool)
        code = np.zeros(N, int)
        px = np.full(N, np.nan)
        exit_k = np.zeros(N, int)
        resolved_path = contiguous_k >= max_hold
        for k in range(1, contiguous_k + 1):
            ck, pk, stop, armed = barrier_step(
                O[i + k], H[i + k], L[i + k], Cl[i + k], stop, p["target"], p["be_trigger"], p["be_stop"],
                armed, is_entry_day=(k == 1), is_last_day=(k == max_hold))
            new = (code == 0) & (ck != 0)
            code[new], px[new], exit_k[new] = ck[new], pk[new], k
        gross = px / entry - 1.0
        # breaks inside the realised path invalidate the label
        clean_path = (br_cum[min(i + contiguous_k, T - 1) + 1] - br_cum[i + 1]) == 0
        done = (code != 0) & clean_path & ~blocked
        if not resolved_path:
            done = done & (code != EXIT_TIME)
        gross = np.where(done, gross, np.nan)
        fill = np.where(blocked, 0.0, 1.0)
        df = pd.DataFrame({
            "tarih": dates[i],
            "ticker": tickers,
            "fwd_ret": fwd * 100.0,
            "barrier_gross": gross * 100.0,
            "barrier_net": gross * 100.0 - C.COST_ROUND_TRIP_PCT,
            "exit_reason": [EXIT_NAMES.get(int(x), None) if d else None for x, d in zip(code, done)],
            "filled": fill,
            "exit_k": np.where(done, exit_k, 0),
            "entry_px": entry,
            "exit_px": np.where(done, px, np.nan),
        })
        df = df[np.isfinite(df["fwd_ret"]) | np.isfinite(df["barrier_gross"])]
        if not df.empty:
            out.append(df)
    if not out:
        return pd.DataFrame(columns=["tarih", "ticker", "fwd_ret", "barrier_gross", "barrier_net", "exit_reason", "filled",
                                     "exit_k", "entry_px", "exit_px"])
    res = pd.concat(out, ignore_index=True)
    res["tarih"] = pd.to_datetime(res["tarih"]).dt.normalize()
    return res
