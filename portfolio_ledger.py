"""Live position ledger: exactly the trade rule used in labels/backtest.

Lifecycle: PENDING_ENTRY (signal at close) -> next session:
  * open gaps to limit-up           -> NOT_FILLED
  * run for the next session missed  -> MISSED (never back-filled with guesses)
  * otherwise                        -> OPEN at that session's open
OPEN -> daily barrier_step -> CLOSED (STOP / TARGET / TIME). Closed rows are frozen.
Corporate actions detected today rescale all price levels of open positions.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

import calendar_tr as cal
import config as C
from labels import EXIT_NAMES, barrier_step, entry_blocked, plan_trade


def add_signals(ledger: pd.DataFrame, scored: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    sel = scored[scored["eligible"]]
    if sel.empty:
        return ledger
    rows = []
    for _, r in sel.iterrows():
        rows.append({
            "signal_date": today, "ticker": r["ticker"], "status": "PENDING_ENTRY",
            "composite": float(r["composite"]), "composite_pct": float(r["composite_pct"]),
            "exp_net_pct": float(r["exp_net_pct"]), "regime_label": r.get("regime_label"),
            "p_risk_off": float(r.get("p_risk_off", np.nan)), "model_version": r.get("model_version"),
            "atr_pct": float(r["atr_pct"]), "stop_dist_pct": float(r["stop_dist_pct"]),
            "size_pct": float(r["size_pct"]), "signal_close": float(r["close"]),
            "expiry_date": cal.add_sessions(today, C.MAX_HOLD), "sessions_held": 0, "breakeven_armed": False,
        })
    new = pd.DataFrame(rows)
    if ledger is None or ledger.empty:
        return new
    keep = ledger[~((ledger["signal_date"] == today) & (ledger["status"] == "PENDING_ENTRY"))]
    return pd.concat([keep, new], ignore_index=True)


def update_ledger(ledger: pd.DataFrame, snap: pd.DataFrame, today: pd.Timestamp, ca_ratios: Dict[str, float]):
    """Process today's EOD bar. Returns (ledger, events)."""
    events = []
    if ledger is None or ledger.empty or snap is None or snap.empty:
        return ledger, events
    L = ledger.copy()
    bars = snap.set_index("ticker")[["open", "high", "low", "close"]].astype(float)

    # corporate actions -> rescale price levels of live positions
    for t, ratio in ca_ratios.items():
        m = (L["ticker"] == t) & L["status"].isin(["OPEN", "PENDING_ENTRY"])
        for c in ("entry_price", "stop_price", "target_price", "signal_close", "last_price"):
            L.loc[m, c] = pd.to_numeric(L.loc[m, c], errors="coerce") * ratio
        if m.any():
            events.append({"ticker": t, "type": "CORP_ACTION_ADJUST", "ratio": round(ratio, 4)})

    for i in L.index:
        st = L.at[i, "status"]
        t = L.at[i, "ticker"]
        if st not in ("PENDING_ENTRY", "OPEN"):
            continue
        if st == "PENDING_ENTRY":
            gap = cal.sessions_after_count(L.at[i, "signal_date"], today)
            if gap == 0:
                continue
            if gap > 1 or t not in bars.index:
                L.at[i, "status"] = "MISSED"
                events.append({"ticker": t, "type": "ENTRY_MISSED"})
                continue
            o = bars.at[t, "open"]
            if bool(entry_blocked(L.at[i, "signal_close"], o)) or not np.isfinite(o):
                L.at[i, "status"] = "NOT_FILLED"
                events.append({"ticker": t, "type": "NOT_FILLED_LIMIT_UP"})
                continue
            p = plan_trade(o, L.at[i, "stop_dist_pct"])
            L.at[i, "status"] = "OPEN"
            L.at[i, "entry_date"] = today
            L.at[i, "entry_price"] = float(o)
            L.at[i, "stop_price"] = float(p["stop"])
            L.at[i, "target_price"] = float(p["target"])
            L.at[i, "expiry_date"] = cal.add_sessions(L.at[i, "signal_date"], C.MAX_HOLD)
            L.at[i, "sessions_held"] = 0
            L.at[i, "mfe_pct"], L.at[i, "mae_pct"] = 0.0, 0.0
            events.append({"ticker": t, "type": "ENTRY", "price": round(float(o), 4)})
            is_entry_day = True
        else:
            is_entry_day = False
        if t not in bars.index:
            continue
        b = bars.loc[t]
        entry = float(L.at[i, "entry_price"])
        p = plan_trade(entry, L.at[i, "stop_dist_pct"])
        held = int(cal.sessions_after_count(L.at[i, "signal_date"], today))
        is_last = held >= C.MAX_HOLD
        code, px, new_stop, armed = barrier_step(
            [b["open"]], [b["high"]], [b["low"]], [b["close"]],
            np.array([float(L.at[i, "stop_price"])]), np.array([float(L.at[i, "target_price"])]),
            np.array([p["be_trigger"]]), np.array([p["be_stop"]]),
            np.array([bool(L.at[i, "breakeven_armed"]) if pd.notna(L.at[i, "breakeven_armed"]) else False]),
            is_entry_day=is_entry_day, is_last_day=is_last)
        L.at[i, "sessions_held"] = held
        L.at[i, "last_date"] = today
        L.at[i, "last_price"] = float(b["close"])
        L.at[i, "mfe_pct"] = max(float(L.at[i, "mfe_pct"] or 0.0), (float(b["high"]) / entry - 1) * 100)
        L.at[i, "mae_pct"] = min(float(L.at[i, "mae_pct"] or 0.0), (float(b["low"]) / entry - 1) * 100)
        if code[0] != 0:
            gross = (float(px[0]) / entry - 1.0) * 100.0
            L.at[i, "status"] = "CLOSED"
            L.at[i, "exit_date"] = today
            L.at[i, "exit_price"] = float(px[0])
            L.at[i, "exit_reason"] = EXIT_NAMES[int(code[0])]
            L.at[i, "gross_ret_pct"] = round(gross, 4)
            L.at[i, "net_ret_pct"] = round(gross - C.COST_ROUND_TRIP_PCT, 4)
            events.append({"ticker": t, "type": f"EXIT_{EXIT_NAMES[int(code[0])]}",
                           "net": round(gross - C.COST_ROUND_TRIP_PCT, 2)})
        else:
            if bool(armed[0]) and not bool(L.at[i, "breakeven_armed"] if pd.notna(L.at[i, "breakeven_armed"]) else False):
                events.append({"ticker": t, "type": "STOP_TO_BREAKEVEN", "stop": round(float(new_stop[0]), 4)})
            L.at[i, "stop_price"] = float(new_stop[0])
            L.at[i, "breakeven_armed"] = bool(armed[0])
    return L, events


def performance_summary(ledger: pd.DataFrame) -> Dict:
    from learner_engine import newey_west
    if ledger is None or ledger.empty:
        return {"closed": 0}
    closed = ledger[ledger["status"] == "CLOSED"].copy()
    out = {
        "open": int((ledger["status"] == "OPEN").sum()),
        "pending": int((ledger["status"] == "PENDING_ENTRY").sum()),
        "not_filled": int(ledger["status"].isin(["NOT_FILLED", "MISSED"]).sum()),
        "closed": int(len(closed)),
    }
    if closed.empty:
        return out
    r = pd.to_numeric(closed["net_ret_pct"], errors="coerce").dropna()
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    by_date = closed.assign(r=r).groupby("signal_date")["r"].mean()
    m, se, t, n = newey_west(by_date, 2)
    out.update({
        "hit_rate_pct": round(float((r > 0).mean() * 100), 2),
        "avg_net_pct": round(float(r.mean()), 3),
        "profit_factor": round(float(gains / losses), 3) if losses > 0 else None,
        "date_clustered_mean": round(m, 3),
        "date_clustered_lcb90": round(m - 1.2816 * se, 3) if np.isfinite(se) else None,
        "n_signal_dates": int(n),
        "exit_mix": closed["exit_reason"].value_counts().to_dict(),
        "last20_avg_net_pct": round(float(r.tail(20).mean()), 3),
    })
    return out


def closed_returns_by_exit(ledger: pd.DataFrame, today=None, lookback_sessions: int = 40) -> pd.Series:
    """Net returns of trades closed within the last `lookback_sessions`, in exit order."""
    if ledger is None or ledger.empty:
        return pd.Series(dtype=float)
    c = ledger[ledger["status"] == "CLOSED"].copy()
    if today is not None and not c.empty:
        today = pd.Timestamp(today)
        window = cal.sessions_between(today - pd.Timedelta(days=int(lookback_sessions * 1.6) + 10), today)
        cutoff = window[-lookback_sessions] if len(window) >= lookback_sessions else window[0]
        c = c[pd.to_datetime(c["exit_date"]) >= cutoff]
    c = c.sort_values("exit_date")
    return pd.to_numeric(c["net_ret_pct"], errors="coerce").dropna().reset_index(drop=True)
