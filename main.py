"""Adaptive BIST Orderflow Meta-Engine V2 - daily end-of-day orchestrator.

Run once per session AFTER the close (GitHub Actions ~18:25 TR):
  1. EOD snapshot (TradingView, free) + optional takas
  2. validation, stale/holiday detection, corporate-action detection
  3. regime forecast (sticky HMM on XU100 + USDTRY, yfinance, free)
  4. labels -> learning (champion/challenger) -> calibration
  5. autonomy guard
  6. ledger update with today's bar (entries/exits)
  7. scoring + selection for TOMORROW's open, Telegram report
`python main.py --self-test` runs the whole pipeline on synthetic data in a
temporary folder (never touches ./data).
"""
from __future__ import annotations

import os
import sys
import tempfile

if "--self-test" in sys.argv and "BOQ_DATA_DIR" not in os.environ:
    os.environ["BOQ_DATA_DIR"] = tempfile.mkdtemp(prefix="boq_selftest_")

import argparse  # noqa: E402
from datetime import datetime  # noqa: E402
from html import escape  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import calendar_tr as cal  # noqa: E402
import config as C  # noqa: E402
import regime_model  # noqa: E402
from autonomy_guard import evaluate_guard  # noqa: E402
from calibration import calibrate  # noqa: E402
from data_integrity import build_price_panel, is_stale, today_ca_ratio, validate_market_frame  # noqa: E402
from flow_fetcher import fetch_all_data  # noqa: E402
from labels import compute_labels  # noqa: E402
from learner_engine import live_composite_ic, run_learning  # noqa: E402
from meta_engine import score_snapshot  # noqa: E402
from portfolio_ledger import add_signals, closed_returns_by_exit, performance_summary, update_ledger  # noqa: E402
from state_manager import (SNAPSHOT_RAW_COLS, append_snapshot, load_ledger, load_research_prior,  # noqa: E402
                           load_snapshots, load_state, save_ledger, save_snapshots, save_state)

KEEP_SCORED_COLS = [f"z_{k}" for k in C.FACTORS] + [
    "composite", "composite_pct", "exp_net_pct", "regime_label", "p_risk_off", "model_version", "eligible"]


def build_dataset(snapshots: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time learning set: factor z-scores recorded on date t + labels resolved later."""
    if snapshots is None or snapshots.empty or "z_" + C.FACTORS[0] not in snapshots.columns:
        return pd.DataFrame()
    panel = build_price_panel(snapshots)
    lab = compute_labels(panel)
    if lab.empty:
        return pd.DataFrame()
    cols = ["tarih", "ticker"] + [c for c in KEEP_SCORED_COLS if c in snapshots.columns and c != "eligible"]
    feat = snapshots[cols].dropna(subset=["z_" + C.FACTORS[0]])
    return feat.merge(lab, on=["tarih", "ticker"], how="inner")


def fill_atr_from_history(valid: pd.DataFrame, hist: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """If the feed did not deliver ATR, compute a simple 14-session ATR from our own stored
    EOD bars (real data, never invented). Names with too little history stay NaN."""
    atr = pd.to_numeric(valid["atr"], errors="coerce") if "atr" in valid else pd.Series(np.nan, index=valid.index)
    if atr.notna().mean() >= 0.5 or hist is None or hist.empty:
        return valid
    h = pd.concat([hist[["tarih", "ticker", "high", "low", "close"]], valid[["tarih", "ticker", "high", "low", "close"]]])
    h = h.sort_values(["ticker", "tarih"])
    prev = h.groupby("ticker")["close"].shift()
    h["tr"] = np.maximum(h["high"] - h["low"], np.maximum((h["high"] - prev).abs(), (h["low"] - prev).abs()))
    last = h.groupby("ticker").tail(n)
    agg = last.groupby("ticker")["tr"].agg(["mean", "count"])
    est = agg.loc[agg["count"] >= n, "mean"]
    out = valid.copy()
    out["atr"] = atr.fillna(out["ticker"].map(est))
    return out


def send_telegram(message: str) -> bool:
    import requests
    token, chat_id = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("CHAT_ID")
    if not token or not chat_id:
        print(message)
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": message[:4000], "parse_mode": "HTML"}, timeout=15)
        return r.status_code == 200
    except Exception:
        return False


def format_report(today, state, scored, info, events, perf) -> str:
    reg, g, model, cal_ = state.get("regime", {}), state.get("autonomy_guard", {}), state.get("model", {}), state.get("calibration", {})
    probs = " / ".join(f"{k} %{v * 100:.0f}" for k, v in (reg.get("probs") or {}).items())
    L = [
        f"🧠 <b>BIST META-ENGINE V2</b> | {today:%d.%m.%Y} (kapanış sonrası)",
        f"🧭 Rejim (HMM): <b>{reg.get('label', '?')}</b> | {probs}",
        f"📈 Beklenen piyasa 5G: <b>%{float(reg.get('exp_mkt_5d_pct', 0)):+.2f}</b>" + (" ⚠️ degraded" if reg.get("degraded") else ""),
        f"🛡️ Guard: <b>{g.get('mode', '?')}</b> ({escape(str(g.get('reason', '')))}) | maruziyet x{info.get('exposure', 0):.2f}",
        f"🧪 Model: {escape(str(model.get('champion_version')))} | {escape(str(model.get('status', '')))}",
        f"🎯 Kalibrasyon: {cal_.get('status')} | eşik p{info.get('pct_cutoff', 0):.0f}",
    ]
    li = state.get("model", {}).get("composite_ic_live", {})
    if li.get("n_dates"):
        L.append(f"📊 Canlı OOS IC: {li.get('ic_mean', 0):+.3f} (t={li.get('t_nw', 0):.2f}, n={li['n_dates']})")
    if perf.get("closed"):
        L.append(f"💼 Kapanan {perf['closed']} işlem | isabet %{perf.get('hit_rate_pct', 0):.1f} | ort. net %{perf.get('avg_net_pct', 0):+.2f} | PF {perf.get('profit_factor')}")
    if events:
        L.append("🔔 <b>Pozisyon olayları</b>")
        for e in events[:12]:
            extra = f" net %{e['net']:+.2f}" if "net" in e else (f" @{e['price']}" if "price" in e else "")
            L.append(f"• {escape(str(e['ticker']))} — {e['type']}{extra}")
    top = scored[scored["eligible"]]
    if not top.empty:
        L.append("💎 <b>YARIN AÇILIŞTA ALIM ADAYLARI</b>")
        for _, r in top.iterrows():
            L.append(f"• <b>{escape(str(r['ticker']))}</b> | skor p{r['composite_pct']:.0f} | beklenen net %{r['exp_net_pct']:+.2f} | "
                     f"stop -%{r['stop_dist_pct']:.1f} / hedef +%{r['stop_dist_pct'] * C.TARGET_RR:.1f} | "
                     f"ağırlık %{r['size_pct']:.1f} | süre {C.MAX_HOLD}G")
    else:
        L.append("ℹ️ Bugün tüm kapıları (beklenen net getiri, likidite, guard) geçen aday yok.")
    return "\n".join(L)


def run(force: bool = False, fetcher=fetch_all_data, today=None) -> dict:
    real_run = today is None
    today = pd.Timestamp(today) if today is not None else cal.today_tr()
    if real_run and not force:
        now_tr = pd.Timestamp.now(tz=C.MARKET_TZ)
        if now_tr.hour * 60 + now_tr.minute < 18 * 60 + 15:
            print(f"ℹ️ Seans henüz kapanmadı ({now_tr:%H:%M} TR). Motor yalnızca 18:15 sonrası kapanış verisiyle çalışır.")
            return {"status": "BEFORE_CLOSE"}
    if not cal.is_session(today) and not force:
        print(f"ℹ️ {today.date()} BIST seansı değil; çalışma atlandı.")
        return {"status": "NOT_SESSION"}

    state = load_state()
    research = load_research_prior()
    snapshots = load_snapshots()

    raw, meta = fetcher(state)
    if not raw.empty:
        raw["tarih"] = today
    valid, quality = validate_market_frame(raw)
    state["data_quality"] = {**quality, "fetch": meta}
    if valid.empty or not quality.get("ok"):
        # No signals today, but do not push the guard into a multi-day SAFE cycle
        # because of a single feed outage.
        state["last_run"] = {"date": str(today.date()), "status": f"DATA_BLOCKED:{quality.get('reason')}",
                             "utc": datetime.utcnow().isoformat() + "Z"}
        save_state(state)
        send_telegram(f"🛑 BIST Meta-Engine: veri kalitesi yetersiz ({escape(str(quality.get('reason')))}). Yeni sinyal üretilmedi.")
        return {"status": "DATA_BLOCKED"}

    hist = snapshots[snapshots["tarih"] < today] if not snapshots.empty else snapshots
    last_date = hist["tarih"].max() if not hist.empty else None
    last = hist[hist["tarih"] == last_date] if last_date is not None else pd.DataFrame()
    if not force and is_stale(valid, last):
        print("ℹ️ Snapshot bir önceki günle aynı (tatil / donmuş veri); çalışma atlandı.")
        state["last_run"] = {"date": str(today.date()), "status": "STALE_SKIPPED"}
        save_state(state)
        return {"status": "STALE"}

    if "sector" in valid:
        state.setdefault("sector_map", {}).update(
            {t: s for t, s in zip(valid["ticker"], valid["sector"]) if isinstance(s, str) and s})
    valid = fill_atr_from_history(valid, hist)
    ca = today_ca_ratio(valid, last, last_date, today) if last_date is not None else {}
    prev_takas = last.set_index("ticker")["takas_conc"] if (not last.empty and "takas_conc" in last) else None

    # ---------------- regime forecast
    regime_model.update_regime(state, valid, today)

    # ---------------- learning on point-in-time history (+ today's bar resolves labels)
    today_raw = valid[[c for c in SNAPSHOT_RAW_COLS if c in valid.columns]].copy()
    panel_src = append_snapshot(hist, today_raw) if not hist.empty else today_raw
    dataset = build_dataset(panel_src)
    run_learning(state, dataset, research)
    calibrate(state, dataset, research)
    state["model"]["composite_ic_live"] = live_composite_ic(dataset)

    # ---------------- ledger with today's bar
    ledger = load_ledger()
    ledger, events = update_ledger(ledger, valid, today, ca)

    # ---------------- guard
    feats = valid.copy()
    feats["atr_pct"] = (pd.to_numeric(feats["atr"], errors="coerce") if "atr" in feats else np.nan) / feats["close"] * 100.0
    guard = evaluate_guard(state, features=feats, data_quality=quality.get("score", 0.0), rows=quality.get("rows"),
                           live_ic=state["model"]["composite_ic_live"], trade_returns=closed_returns_by_exit(ledger, today))

    # ---------------- scoring for tomorrow
    live = ledger[ledger["status"].isin(["OPEN", "PENDING_ENTRY"])] if not ledger.empty else ledger
    scored, info = score_snapshot(valid, state, prev_takas, guard, open_positions=len(live),
                                  held_tickers=set(live["ticker"]) if not live.empty else set())
    ledger = add_signals(ledger, scored, today)

    keep = [c for c in SNAPSHOT_RAW_COLS if c in scored.columns] + [c for c in KEEP_SCORED_COLS if c in scored.columns]
    snap_today = scored[keep].copy()
    snap_today["tarih"] = today
    save_snapshots(append_snapshot(hist, snap_today))
    save_ledger(ledger)

    perf = performance_summary(ledger)
    state["performance"] = perf
    state["last_run"] = {"date": str(today.date()), "status": "OK", "n_eligible": info["n_eligible"],
                         "weights": info["weights"], "coverage": info["coverage"], "ca_events_today": ca,
                         "missing_sessions": [str(d.date()) for d in cal.missing_sessions(
                             list(hist["tarih"].unique()) + [today])][-10:] if not hist.empty else [],
                         "utc": datetime.utcnow().isoformat() + "Z"}
    save_state(state)
    send_telegram(format_report(today, state, scored, info, events, perf))
    print(f"✅ Tamamlandı | rejim={state['regime'].get('label')} | guard={guard['mode']} | aday={info['n_eligible']}")
    return {"status": "OK", "state": state, "scored": scored, "info": info, "events": events}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--force", action="store_true", help="tatil/stale kontrolünü atla (manuel test)")
    args = ap.parse_args()
    if args.self_test:
        from selftest import run_self_test
        ok = run_self_test()
        sys.exit(0 if ok else 1)
    run(force=args.force)


if __name__ == "__main__":
    main()
