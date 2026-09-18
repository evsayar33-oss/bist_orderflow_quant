"""Adaptive BIST Orderflow Meta-Engine V1 - production orchestrator."""
from __future__ import annotations

import argparse
from datetime import datetime
import os

import numpy as np
import pandas as pd

from data_integrity import validate_market_frame
from flow_fetcher import fetch_all_data
from learner_engine import resolve_forward_outcomes
from longterm_auditor import audit_and_calibrate
from meta_engine import score_market
from state_manager import load_ai_state, load_signal_log, load_lifecycle_signals, save_ai_state, save_lifecycle_signals

GECMIS_DOSYA = "gecmis_veri.csv"


def load_history() -> pd.DataFrame:
    if not os.path.exists(GECMIS_DOSYA):
        return pd.DataFrame()
    try:
        df = pd.read_csv(GECMIS_DOSYA)
        if "tarih" in df.columns:
            df["tarih"] = pd.to_datetime(df["tarih"], errors="coerce").dt.normalize()
        return df
    except Exception as exc:
        print(f"⚠️ Geçmiş veri okuma hatası: {exc}")
        return pd.DataFrame()


def log_signals(scored: pd.DataFrame, state: dict) -> pd.DataFrame:
    existing = load_signal_log()
    if scored is None or scored.empty:
        return existing
    leaders = scored[scored["eligible"]].copy().sort_values("meta_score", ascending=False).head(int(state["risk_guards"]["max_candidates"]))
    if leaders.empty:
        return existing
    rows = []
    today = pd.Timestamp.now(tz="Europe/Istanbul").normalize().tz_localize(None)
    for _, r in leaders.iterrows():
        rows.append({
            "tarih": today,
            "ticker": r["ticker"],
            "entry_price": float(r["close"]),
            "meta_score": float(r["meta_score"]),
            "pre_move_score": float(r["pre_move_score"]),
            "flow_score": float(r["flow_score"]),
            "resilience_score": float(r["resilience_score"]),
            "regime_fit_score": float(r["regime_fit_score"]),
            "quality_score": float(r["quality_score"]),
            "risk_score": float(r["risk_score"]),
            "data_quality": float(r["quality_score"]),
            "market_regime": r["regime"],
            "regime_confidence": float(r["regime_confidence"]),
            "model_version": r.get("model_version", "champion-1"),
            "ret_t1": np.nan, "ret_t3": np.nan, "ret_t5": np.nan, "ret_t10": np.nan,
            "mfe_t10": np.nan, "mae_t10": np.nan, "outcome": "PENDING",
        })
    new = pd.DataFrame(rows)
    if not existing.empty:
        existing["tarih"] = pd.to_datetime(existing["tarih"], errors="coerce").dt.normalize()
        existing = existing[~existing["tarih"].eq(today)]
        combined = pd.concat([existing, new], ignore_index=True)
    else:
        combined = new
    return combined.drop_duplicates(subset=["tarih", "ticker"], keep="last")


def log_history(scored: pd.DataFrame) -> None:
    if scored is None or scored.empty:
        return
    old = load_history()
    today = pd.Timestamp.now(tz="Europe/Istanbul").normalize().tz_localize(None)
    new = scored.copy()
    new["tarih"] = today
    if not old.empty:
        old = old[~pd.to_datetime(old["tarih"], errors="coerce").dt.normalize().eq(today)]
        all_df = pd.concat([old, new], ignore_index=True)
    else:
        all_df = new
    all_df.to_csv(GECMIS_DOSYA, index=False)



def upsert_lifecycle(scored: pd.DataFrame, state: dict) -> None:
    if scored is None or scored.empty:
        return
    current = load_lifecycle_signals()
    today = pd.Timestamp.now(tz="Europe/Istanbul").normalize().tz_localize(None)
    leaders = scored[scored["eligible"]].head(int(state["risk_guards"]["max_candidates"]))
    if leaders.empty:
        return
    rows = []
    for _, r in leaders.iterrows():
        rows.append({
            "tarih": today, "ticker": r["ticker"], "entry_price": float(r["close"]),
            "initial_stop_price": round(float(r["close"]) * 0.92, 4),
            "current_stop_price": round(float(r["close"]) * 0.92, 4),
            "target_price": round(float(r["close"]) * 1.20, 4), "last_seen_price": float(r["close"]),
            "meta_score": float(r["meta_score"]), "pre_move_score": float(r["pre_move_score"]),
            "flow_score": float(r["flow_score"]), "resilience_score": float(r["resilience_score"]),
            "regime_fit_score": float(r["regime_fit_score"]), "quality_score": float(r["quality_score"]),
            "risk_score": float(r["risk_score"]), "data_quality": float(r["quality_score"]),
            "market_regime": r["regime"], "regime_confidence": float(r["regime_confidence"]),
            "model_version": r.get("model_version", "champion-1"), "peak_price": float(r["close"]),
            "trough_price": float(r["close"]), "max_adverse_excursion": 0.0, "max_favorable_excursion": 0.0,
            "ret_t1": np.nan, "ret_t3": np.nan, "ret_t5": np.nan, "ret_t10": np.nan, "outcome": "OPEN"
        })
    new = pd.DataFrame(rows)
    if current.empty:
        combined = new
    else:
        current = current[~((current["tarih"] == today) & current["ticker"].isin(new["ticker"]))]
        combined = pd.concat([current, new], ignore_index=True)
    save_lifecycle_signals(combined.drop_duplicates(subset=["tarih", "ticker"], keep="last"))

def send_telegram(message: str) -> bool:
    import requests
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def format_report(scored: pd.DataFrame, state: dict, alerts) -> str:
    regime = state.get("market_regime", "NEUTRAL")
    confidence = state.get("regime_confidence", 0)
    stats = state.get("regime_stats", {})
    msg = [
        f"🧠 <b>ADAPTIVE BIST ORDERFLOW META-ENGINE V1</b> | {datetime.now().strftime('%d.%m.%Y')}",
        f"🧭 Rejim: <b>{regime}</b> | Güven: <b>%{confidence:.1f}</b>",
        f"🛡️ Otonomi: <b>{state.get('autonomy_guard', {}).get('mode', 'NORMAL')}</b> | Maruziyet x{state.get('autonomy_guard', {}).get('exposure_multiplier', 1.0):.2f}",
        f"📊 Breadth Up: %{stats.get('breadth_up', 0)*100:.1f} | Down: %{stats.get('breadth_down', 0)*100:.1f}",
        f"🛡️ Aktif Model: <b>{state.get('validation', {}).get('active_model_version', 'champion-1')}</b>",
        f"📚 Audit Win T+3: <b>%{state.get('audit_summary', {}).get('win_rate_t3', 0):.1f}</b>",
    ]
    if alerts:
        msg.append("🚨 <b>RİSK UYARILARI</b>")
        for a in alerts[:8]:
            msg.append(f"• {a.get('ticker')} — {a.get('type')}")
    if scored is not None and not scored.empty:
        top = scored[scored["eligible"]].head(8)
        if not top.empty:
            msg.append("💎 <b>QUALIFIED PRE-MOVE CANDIDATES</b>")
            for _, r in top.iterrows():
                msg.append(
                    f"• <b>{r['ticker']}</b> | Meta {r['meta_score']:.1f} | PreMove {r['pre_move_score']:.1f} | Flow {r['flow_score']:.1f} | Risk {r['risk_score']:.1f}"
                )
        else:
            msg.append("ℹ️ Bugün güvenlik eşiklerini geçen aday yok.")
    return "\n".join(msg)


def self_test():
    n = 120
    rng = np.random.default_rng(42)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    high = close * (1 + rng.uniform(0, 0.02, n))
    low = close * (1 - rng.uniform(0, 0.02, n))
    open_ = (high + low) / 2
    d = pd.DataFrame({
        "ticker": [f"T{i:03d}" for i in range(n)], "close": close, "open": open_, "high": high, "low": low,
        "volume": rng.integers(100000, 500000, n), "change_%": rng.normal(0, 1.5, n), "value_traded": rng.uniform(10e6, 200e6, n),
        "high_1m": high * 1.1, "low_1m": low * 0.9, "rvol": rng.uniform(0.6, 2.2, n), "perf_w": rng.normal(0, 4, n),
        "perf_1m": rng.normal(0, 8, n), "perf_y": rng.normal(0, 20, n), "roe": rng.uniform(10, 35, n),
        "pb": rng.uniform(0.6, 5, n), "pe": rng.uniform(4, 25, n), "market_cap": rng.uniform(2e9, 40e9, n), "oper_margin": rng.uniform(4, 25, n),
        "foreign_ratio": np.nan, "foreign_ratio_confidence": 0.0,
    })
    valid, summary = validate_market_frame(d, min_rows=30)
    state = load_ai_state()
    scored = score_market(valid, state)
    assert summary["ok"] and not scored.empty
    assert "meta_score" in scored.columns and "risk_score" in scored.columns
    assert not bool((scored["eligible"] & (scored["meta_score"] < state["risk_guards"]["min_signal_score"])).any())
    print("✅ SELF-TEST PASSED")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🧠 Adaptive BIST Orderflow Meta-Engine V1 başlatılıyor...")
    state = load_ai_state()
    history = load_history()

    if not history.empty:
        signals = load_signal_log()
        if not signals.empty:
            resolved = resolve_forward_outcomes(signals, history)
            resolved.to_csv("signals_log.csv", index=False)

    state, alerts = audit_and_calibrate(history=history)

    current = fetch_all_data()
    valid, quality = validate_market_frame(current, min_rows=30)
    if valid.empty or not quality.get("ok", False):
        state["data_quality"] = quality
        state["audit_summary"]["status"] = f"DATA_BLOCKED ({quality.get('reason', 'quality')})"
        save_ai_state(state)
        print("🛑 Veri kalitesi güvenli eşiği geçemedi; sinyal üretimi durduruldu.")
        return

    state["data_quality"] = quality
    scored = score_market(valid, state)
    signal_log_for_guard = load_signal_log()
    from autonomy_guard import evaluate_autonomy_guard
    guard_result = evaluate_autonomy_guard(
        state,
        features=scored,
        regime=state.get("market_regime"),
        regime_confidence=float(state.get("regime_confidence", 0.0)) / 100.0,
        performance_returns=(signal_log_for_guard["ret_t3"] if "ret_t3" in signal_log_for_guard.columns else None),
        data_quality_score=float(quality.get("score", 0.0)),
        row_count=len(valid),
        min_rows=30,
        project="orderflow",
    )
    base_threshold = float(state.get("risk_guards", {}).get("min_signal_score", 72.0))
    effective_threshold = base_threshold + float(guard_result.get("signal_threshold_add", 0.0))
    if guard_result.get("block_new_entries"):
        scored["eligible"] = False
    else:
        scored["eligible"] = scored["eligible"].astype(bool) & (
            pd.to_numeric(scored["meta_score"], errors="coerce") >= effective_threshold
        )

    signal_log = log_signals(scored, state)
    signal_log.to_csv("signals_log.csv", index=False)
    upsert_lifecycle(scored, state)
    log_history(scored)

    report = format_report(scored, state, alerts)
    send_telegram(report)
    save_ai_state(state)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Tamamlandı | Rejim={state.get('market_regime')} | Aday={int(scored['eligible'].sum())}")


if __name__ == "__main__":
    main()
