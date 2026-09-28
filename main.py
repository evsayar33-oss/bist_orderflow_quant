"""Adaptive BIST Real-Return Engine V3 — daily run after the close (18:25 TR).

Every session:
  * execute yesterday's orders at today's OPEN, mark the portfolio to the CLOSE
    (adjusted change -> bonus issues are harmless), catastrophe-stop check,
  * NAV vs XU100 vs CPI bookkeeping, autonomy guard.
First session of every month (the monthly review):
  * price factors from adjusted yfinance history + TradingView fundamentals,
  * resolve 12-month labels of past monthly snapshots (nominal, REAL, vs XU100),
  * learning (champion/challenger) + calibration of expected REAL return,
  * HOLD / SELL (thesis) / BUY decisions -> orders for the next open.
`python main.py --self-test` runs everything on synthetic data in a temp folder.
"""
from __future__ import annotations

import os
import sys
import tempfile

if "--self-test" in sys.argv and "BOQ_DATA_DIR" not in os.environ:
    os.environ["BOQ_DATA_DIR"] = tempfile.mkdtemp(prefix="boq3_selftest_")

import argparse  # noqa: E402
from datetime import datetime  # noqa: E402
from html import escape  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import calendar_tr as cal  # noqa: E402
import config as C  # noqa: E402
import inflation as INF  # noqa: E402
import market_data as MD  # noqa: E402
import regime_model as RM  # noqa: E402
from autonomy_guard import evaluate_guard  # noqa: E402
from backtest_validator import enrich_lots, lot_metrics, nav_metrics  # noqa: E402
from calibration import calibrate  # noqa: E402
from data_integrity import validate_market_frame  # noqa: E402
from factors import price_factors_at, wide_from_history  # noqa: E402
from labels import forward_labels  # noqa: E402
from learner_engine import daily_rank_ic, live_composite_ic, newey_west, run_learning  # noqa: E402
from meta_engine import exposure_from, plan_rebalance, score_universe  # noqa: E402
from portfolio import apply_day, new_portfolio, weights as pf_weights  # noqa: E402
from state_manager import (append_rows, load_monthly_snapshots, load_nav, load_research_prior,  # noqa: E402
                           load_state, load_trade_log, save_monthly_snapshots, save_state)

FUND_COLS = ["ticker", "market_cap", "sector", "roe", "pe", "pb", "ps", "net_income", "revenue",
             "rev_growth", "op_margin", "debt_to_equity", "div_yield"]
LABEL_COLS = ["fwd_1m", "fwd_3m", "fwd_ret", "real_ret", "xu_excess", "cpi_12m_pct"]


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


def _fingerprint(df: pd.DataFrame) -> str:
    top = df.sort_values("value_traded", ascending=False).head(60)
    return f"{np.round(top['close'].to_numpy(float), 4).sum():.4f}|{np.round(top['volume'].to_numpy(float), 0).sum():.0f}"


def resolve_labels(snaps: pd.DataFrame, wide, cpi, index_close) -> pd.DataFrame:
    """Fill 1m/3m/12m labels of past monthly snapshots once the windows have elapsed."""
    if snaps.empty:
        return snaps
    s = snaps.copy()
    for c in LABEL_COLS:
        if c not in s.columns:
            s[c] = np.nan
    need = s[s["fwd_ret"].isna() | s["fwd_3m"].isna() | s["fwd_1m"].isna()]["tarih"].unique()
    if len(need) and wide is not None:
        lab = forward_labels(wide, list(need), cpi, index_close)
        if not lab.empty:
            lab = lab.rename(columns={c: f"{c}__new" for c in LABEL_COLS})
            s = s.merge(lab, on=["tarih", "ticker"], how="left")
            for c in LABEL_COLS:
                s[c] = s[c].fillna(s[f"{c}__new"])
            s = s.drop(columns=[f"{c}__new" for c in LABEL_COLS])
    # real return can resolve later than the nominal one (CPI publication lag)
    m = s["fwd_ret"].notna() & s["real_ret"].isna()
    if m.any() and cpi is not None and len(cpi):
        cr = INF.cpi_ratio_vec(cpi, s.loc[m, "tarih"], s.loc[m, "tarih"] + pd.DateOffset(months=12))
        s.loc[m, "real_ret"] = ((1 + s.loc[m, "fwd_ret"] / 100.0) / cr - 1.0) * 100.0
        s.loc[m, "cpi_12m_pct"] = (cr - 1.0) * 100.0
    return s


def ic_stats(dataset: pd.DataFrame, target: str) -> dict:
    if dataset is None or dataset.empty or target not in dataset:
        return {"n_dates": 0}
    d = dataset.dropna(subset=[target, "composite"])
    ic = daily_rank_ic(d, ["composite"], target)
    if ic.empty:
        return {"n_dates": 0}
    s = ic["composite"]
    lag = 11 if target == "fwd_ret" else (2 if target == "fwd_3m" else 0)
    m, se, t, n = newey_west(s, lag)
    return {"n_dates": n, "ic_mean": round(m, 4), "t_nw": round(t, 2), "ic_recent": round(float(s.tail(6).mean()), 4)}


def monthly_review(state, research, snap, today, index_close, cpi, cpi_stats, guard, hist_fn):
    pf = state["portfolio"]
    snaps = load_monthly_snapshots()
    universe = snap.sort_values("value_traded", ascending=False)["ticker"].head(C.SCAN_LIMIT).tolist()
    universe = sorted(set(universe) | set(pf["positions"].keys()))
    start = today - pd.Timedelta(days=430)
    unresolved = []
    if not snaps.empty:
        pend = snaps[snaps.get("fwd_ret").isna()] if "fwd_ret" in snaps else snaps
        pend = pend[pend["tarih"] >= today - pd.Timedelta(days=500)]
        if not pend.empty:
            start = min(start, pend["tarih"].min() - pd.Timedelta(days=10))
            unresolved = sorted(set(pend["ticker"]))
    hist = hist_fn(sorted(set(universe) | set(unresolved)), str(start.date()))
    if len(hist) < C.MIN_CROSS_SECTION:
        return {"status": "HISTORY_UNAVAILABLE", "n_hist": len(hist)}, []
    wide = wide_from_history(hist)
    snaps = resolve_labels(snaps, wide, cpi, index_close)

    dataset = snaps.dropna(subset=["fwd_ret"]) if not snaps.empty and "fwd_ret" in snaps else pd.DataFrame()
    run_learning(state, dataset, research)
    calibrate(state, dataset, research)
    state["model"]["ic_live_12m"] = ic_stats(snaps, "fwd_ret")
    state["model"]["ic_live_3m"] = ic_stats(snaps, "fwd_3m")

    price_f = price_factors_at(wide, index_close, today)
    price_f = price_f[price_f["ticker"].isin(universe)]
    fund = snap[[c for c in FUND_COLS if c in snap.columns]].copy()
    frame, info = score_universe(price_f, fund, state, cpi_stats, state.get("sector_map"))
    if frame.empty:
        return {"status": "NO_FACTORS", **info}, []
    exposure = exposure_from(guard, state.get("regime", {}))
    chg = snap.set_index("ticker")["change_pct"]
    orders, summ = plan_rebalance(pf, frame, state, exposure, block=bool(guard.get("block_new_entries")),
                                  today_change=chg)
    keep = [o for o in pf["pending"] if o.get("reason") == "CATASTROPHE_STOP"]
    pf["pending"] = keep + [o for o in orders if o["ticker"] not in {k["ticker"] for k in keep}]
    pf["last_rebalance_month"] = today.strftime("%Y-%m")

    sel = set(summ.get("holds", [])) | set(summ.get("buys", []))
    cols = ["ticker", "sector", "close_adj", "med_value_traded", "vol_ann_pct", "beta", "composite", "composite_pct",
            "exp_real_12m", "p_beat_cpi", "regime_label", "model_version", "fund_break"] + \
        [f"f_{k}" for k in C.FACTORS] + [f"z_{k}" for k in C.FACTORS]
    rec = frame[[c for c in cols if c in frame.columns]].copy()
    rec["tarih"] = today
    rec["selected"] = rec["ticker"].isin(sel)
    for c in LABEL_COLS:
        rec[c] = np.nan
    base = snaps[snaps["tarih"] != today] if not snaps.empty else snaps
    save_monthly_snapshots(pd.concat([base, rec], ignore_index=True) if not base.empty else rec)
    top = frame[frame["ticker"].isin(summ.get("buys", []))].sort_values("composite", ascending=False)
    return {"status": "OK", **info, **summ, "exposure": round(exposure, 3)}, top.to_dict("records")


def format_monthly(today, state, rev, top) -> str:
    reg, g, m, cal_, inf = (state.get(k, {}) for k in ("regime", "autonomy_guard", "model", "calibration", "inflation"))
    perf = state.get("performance", {})
    L = [f"🏛️ <b>BIST REEL GETİRİ MOTORU V3 — AYLIK GÖZDEN GEÇİRME</b> | {today:%d.%m.%Y}",
         f"🎯 Hedef: 12 ayda TÜFE'yi yenmek (ikincil: XU100)",
         f"📉 TÜFE yıllık %{inf.get('yoy_pct')} | beklenen 12A %{inf.get('expected_12m_pct')} ({inf.get('source')})",
         f"🧭 Rejim: <b>{reg.get('label')}</b> | P(risk-off) %{float(reg.get('p_risk_off') or 0) * 100:.0f} | "
         f"piyasa 12A beklenti %{rev.get('market_12m', {}).get('mkt_12m_pct')}",
         f"🛡️ Guard: {g.get('mode')} | maruziyet %{float(rev.get('exposure', 0)) * 100:.0f}",
         f"🧠 Model: {escape(str(m.get('champion_version')))} | {escape(str(m.get('status')))}",
         f"🎚️ Kalibrasyon: {cal_.get('status')} | alım eşiği p{cal_.get('pct_cutoff')} / tutma p{C.HOLD_PCT:.0f}"]
    if rev.get("sells"):
        L.append("🔻 <b>SAT (yarın açılış):</b> " + ", ".join(rev["sells"]))
    if rev.get("holds"):
        L.append("✅ <b>TUT:</b> " + ", ".join(rev["holds"]))
    if top:
        L.append("💎 <b>AL (yarın açılış):</b>")
        tw = rev.get("target_weights", {})
        for r in top:
            pb = r.get("p_beat_cpi")
            L.append(f"• <b>{escape(str(r['ticker']))}</b> | skor p{r['composite_pct']:.0f} | beklenen reel 12A "
                     f"%{r['exp_real_12m']:+.1f}" + (f" | TÜFE'yi yenme olasılığı %{pb * 100:.0f}" if pb == pb and pb is not None else "")
                     + f" | ağırlık %{tw.get(r['ticker'], 0) * 100:.1f}")
    elif not rev.get("blocked"):
        L.append("ℹ️ Bu ay yeni alım kriterlerini (beklenen reel getiri, likidite, sektör limiti) geçen hisse yok.")
    L.append(f"💵 Hedef nakit: %{float(rev.get('cash_target', 0)) * 100:.0f}")
    if perf.get("nav", {}).get("days"):
        n = perf["nav"]
        L.append(f"📊 Portföy: toplam %{n.get('total_return_pct')} | reel %{n.get('real_total_pct')} | "
                 f"XU100 %{n.get('xu100_total_pct')} | maks. düşüş %{n.get('max_drawdown_pct')}")
    return "\n".join(L)


def run(force: bool = False, today=None, fetch=MD.fetch_snapshot, hist_fn=MD.download_history,
        cpi_fn=INF.load_cpi, regime_fn=RM.download_regime_series) -> dict:
    real_run = today is None
    today = pd.Timestamp(today) if today is not None else cal.today_tr()
    if real_run and not force:
        now_tr = pd.Timestamp.now(tz=C.MARKET_TZ)
        if now_tr.hour * 60 + now_tr.minute < 18 * 60 + 15:
            print(f"ℹ️ Seans kapanmadı ({now_tr:%H:%M}). Motor 18:15 sonrası kapanış verisiyle çalışır.")
            return {"status": "BEFORE_CLOSE"}
    if not cal.is_session(today) and not force:
        print(f"ℹ️ {today.date()} BIST seansı değil.")
        return {"status": "NOT_SESSION"}

    state = load_state()
    research = load_research_prior()
    raw, meta = fetch(state)
    if not raw.empty:
        raw["tarih"] = today
    snap, quality = validate_market_frame(raw)
    state["data_quality"] = {**quality, "fetch": meta}
    if snap.empty or not quality.get("ok"):
        state["last_run"] = {"date": str(today.date()), "status": f"DATA_BLOCKED:{quality.get('reason')}"}
        save_state(state)
        send_telegram(f"🛑 BIST V3: veri kalitesi yetersiz ({escape(str(quality.get('reason')))}); bugün işlem yapılmadı.")
        return {"status": "DATA_BLOCKED"}
    fp = _fingerprint(snap)
    if not force and state.get("last_run", {}).get("fingerprint") == fp:
        print("ℹ️ Veri bir önceki çalışmayla aynı (tatil/donmuş veri); atlandı.")
        return {"status": "STALE"}
    if "sector" in snap:
        state.setdefault("sector_map", {}).update({t: s for t, s in zip(snap["ticker"], snap["sector"]) if isinstance(s, str) and s})

    # regime + index
    index_close = None
    try:
        rser = regime_fn()
        index_close = rser["idx"]
    except Exception as exc:
        rser = None
        print(f"⚠️ Rejim serisi alınamadı: {exc}")
    RM.update_regime(state, snap, today, series=rser)

    # inflation
    cpi, cmeta = cpi_fn()
    cpi_stats = INF.inflation_stats(cpi)
    state["inflation"] = {**cpi_stats, **cmeta}

    # portfolio: execute pending orders at today's open, mark to close
    if not state.get("portfolio"):
        state["portfolio"] = new_portfolio(today)
    pf = state["portfolio"]
    bars = snap.set_index("ticker")[["open", "close", "change_pct"]].rename(columns={"change_pct": "chg_pct"})
    events, lots = apply_day(pf, bars, today)
    if lots:
        append_rows(C.TRADE_LOG_FILE, lots)
    xu = float(index_close.iloc[-1]) if index_close is not None and len(index_close) else np.nan
    wts = pf_weights(pf)
    append_rows(C.NAV_FILE, [{"tarih": str(today.date()), "nav": round(pf["nav"], 6), "cash": round(pf["cash"], 6),
                              "n_positions": len(pf["positions"]), "exposure": round(sum(wts.values()), 4),
                              "xu100": xu}])

    nav_df = load_nav()
    guard = evaluate_guard(state, features=snap, data_quality=quality.get("score", 0.0), rows=quality.get("rows"),
                           live_ic=state.get("model", {}).get("ic_live_3m"), nav_df=nav_df)

    review, top = None, []
    if pf.get("last_rebalance_month") != today.strftime("%Y-%m"):
        review, top = monthly_review(state, research, snap, today, index_close, cpi, cpi_stats, guard, hist_fn)
        state["last_rebalance"] = {"date": str(today.date()), **{k: v for k, v in review.items() if k not in ("weights",)}}

    lots_all = enrich_lots(load_trade_log(), cpi, index_close)
    state["performance"] = {"nav": nav_metrics(nav_df, cpi), "lots": lot_metrics(lots_all),
                            "open_positions": {t: {"w": round(wts.get(t, 0), 4), "ret_pct": round((p["level"] - 1) * 100, 2),
                                                   "since": p["entry_date"]} for t, p in pf["positions"].items()}}
    state["last_run"] = {"date": str(today.date()), "status": "OK", "fingerprint": fp,
                         "monthly_review": bool(review), "utc": datetime.utcnow().isoformat() + "Z"}
    save_state(state)

    if review and review.get("status") == "OK":
        send_telegram(format_monthly(today, state, review, top))
    elif review:
        send_telegram(f"⚠️ BIST V3 aylık gözden geçirme tamamlanamadı: {escape(str(review.get('status')))}. Yarın tekrar denenecek.")
        pf["last_rebalance_month"] = None
        save_state(state)
    elif events:
        lines = [f"🔔 <b>BIST V3 portföy olayları</b> | {today:%d.%m.%Y}"]
        for e in events[:15]:
            extra = f" %{e['ret']:+.1f}" if "ret" in e else ""
            lines.append(f"• {escape(str(e['ticker']))} — {e['type']}{extra}")
        send_telegram("\n".join(lines))
    print(f"✅ {today.date()} | NAV {pf['nav']:.4f} | pozisyon {len(pf['positions'])} | aylık={'evet' if review else 'hayır'}")
    return {"status": "OK", "state": state, "events": events, "review": review}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        from selftest import run_self_test
        sys.exit(0 if run_self_test() else 1)
    run(force=a.force)


if __name__ == "__main__":
    main()
