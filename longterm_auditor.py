"""Lifecycle audit, MAE/MFE measurement and controlled feedback loop."""
from __future__ import annotations

from datetime import datetime
import os
import numpy as np
import pandas as pd
import requests

from learner_engine import apply_learning, resolve_forward_outcomes
from state_manager import load_ai_state, load_lifecycle_signals, load_signal_log, save_ai_state, save_lifecycle_signals
from autonomy_guard import evaluate_autonomy_guard


def fetch_current_snapshot():
    url = "https://scanner.tradingview.com/turkey/scan"
    payload = {
        "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
        "columns": ["name", "close", "high", "low", "change"],
        "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"},
        "range": [0, 500],
    }
    try:
        r = requests.post(url, json=payload, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        out = {}
        for item in r.json().get("data", []):
            d = item.get("d", [])
            if len(d) >= 5 and d[0] is not None and d[1] is not None:
                out[d[0]] = {
                    "close": float(d[1]), "high": float(d[2] or d[1]), "low": float(d[3] or d[1]), "change": float(d[4] or 0.0)
                }
        return out
    except Exception as exc:
        print(f"⚠️ Lifecycle snapshot hatası: {exc}")
        return {}


def update_lifecycle(signal_df, snapshot):
    if signal_df.empty or not snapshot:
        return signal_df, []
    df = signal_df.copy()
    alerts = []
    today = pd.Timestamp.now(tz="Europe/Istanbul").normalize().tz_localize(None)
    for i, row in df.iterrows():
        t = row.get("ticker")
        if t not in snapshot:
            continue
        p = snapshot[t]
        entry = float(row.get("entry_price") or 0)
        if entry <= 0:
            continue
        curr = float(p["close"])
        high = float(p["high"])
        low = float(p["low"])
        df.at[i, "last_seen_price"] = curr
        prev_peak = float(row.get("peak_price") or entry)
        prev_trough = float(row.get("trough_price") or entry)
        peak = max(prev_peak, high)
        trough = min(prev_trough, low)
        df.at[i, "peak_price"] = peak
        df.at[i, "trough_price"] = trough
        df.at[i, "max_favorable_excursion"] = round((peak / entry - 1) * 100, 3)
        df.at[i, "max_adverse_excursion"] = round((trough / entry - 1) * 100, 3)
        current_stop = float(row.get("current_stop_price") or row.get("initial_stop_price") or entry * 0.92)
        if df.at[i, "max_favorable_excursion"] >= 8:
            current_stop = max(current_stop, entry * 1.01)
        if df.at[i, "max_favorable_excursion"] >= 15:
            current_stop = max(current_stop, entry * 1.06)
        if df.at[i, "max_favorable_excursion"] >= 25:
            current_stop = max(current_stop, entry * 1.15)
        if df.at[i, "max_favorable_excursion"] >= 40:
            current_stop = max(current_stop, entry * 1.28)
        if current_stop > float(row.get("current_stop_price") or 0):
            alerts.append({"ticker": t, "type": "STOP_RAISE", "stop": round(current_stop, 2)})
        df.at[i, "current_stop_price"] = round(current_stop, 2)
        if curr <= current_stop and row.get("outcome", "OPEN") in ("OPEN", "INCUBATING", "PENDING"):
            df.at[i, "outcome"] = "STOP_TRIGGERED"
            alerts.append({"ticker": t, "type": "STOP_TRIGGERED", "price": round(curr, 2)})
    return df, alerts


def send_telegram_alerts(alerts):
    import os
    if not alerts:
        return False
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if not token or not chat_id:
        return False
    lines = ["🧪 <b>ADAPTIVE BIST META-ENGINE AUDIT</b>"]
    for a in alerts[:12]:
        stop = a.get("stop")
        suffix = f" | Stop {stop}" if stop is not None else ""
        lines.append(f"• <b>{a.get('ticker','?')}</b> — {a.get('type','ALERT')}{suffix}")
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "HTML"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def audit_and_calibrate(history=None):
    state = load_ai_state()
    if history is None:
        try:
            if os.path.exists("gecmis_veri.csv"):
                history = pd.read_csv("gecmis_veri.csv")
                history["tarih"] = pd.to_datetime(history["tarih"], errors="coerce").dt.normalize()
        except Exception:
            history = pd.DataFrame()
    lifecycle = load_lifecycle_signals()
    snapshot = fetch_current_snapshot()
    alerts = []
    if not lifecycle.empty and snapshot:
        lifecycle, alerts = update_lifecycle(lifecycle, snapshot)
        save_lifecycle_signals(lifecycle)

    if history is not None and not history.empty:
        signal_log = history.copy()
        # Outcomes are resolved against persisted daily history, not calendar-day elapsed time.
        signal_log = resolve_forward_outcomes(signal_log, history)
        state = apply_learning(signal_log, state)
        evaluate_autonomy_guard(
            state,
            features=None,
            regime=state.get("market_regime"),
            regime_confidence=float(state.get("regime_confidence", 0.0)) / 100.0,
            performance_returns=(signal_log["ret_t3"] if "ret_t3" in signal_log.columns else None),
            data_quality_score=float(state.get("data_quality", {}).get("score", 100.0)),
            row_count=state.get("data_quality", {}).get("rows"),
            min_rows=30,
            project="orderflow",
        )
    else:
        state["audit_summary"]["status"] = state["audit_summary"].get("status", "LEARNING")
        save_ai_state(state)

    return state, alerts


if __name__ == "__main__":
    history = pd.DataFrame()
    try:
        if os.path.exists("gecmis_veri.csv"):
            history = pd.read_csv("gecmis_veri.csv")
            if "tarih" in history.columns:
                history["tarih"] = pd.to_datetime(history["tarih"], errors="coerce").dt.normalize()
    except Exception:
        pass
    state, alerts = audit_and_calibrate(history=history)
    send_telegram_alerts(alerts)
