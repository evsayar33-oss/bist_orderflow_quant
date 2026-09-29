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
import benchmarks as BM  # noqa: E402
import telegram_report as TG  # noqa: E402
from autonomy_guard import evaluate_guard  # noqa: E402
from backtest_validator import enrich_lots, lot_metrics, nav_metrics  # noqa: E402
from calibration import calibrate  # noqa: E402
from data_integrity import validate_market_frame  # noqa: E402
from factors import price_factors_at, wide_from_history  # noqa: E402
from labels import forward_labels  # noqa: E402
from learner_engine import daily_rank_ic, live_composite_ic, newey_west, run_learning  # noqa: E402
from meta_engine import GOLD_TICKER, default_strategy, exposure_from, plan_rebalance, score_universe  # noqa: E402
import strategy_lab as LAB  # noqa: E402
from state_manager import read_json  # noqa: E402
from portfolio import apply_day, new_portfolio, weights as pf_weights  # noqa: E402
from state_manager import (append_rows, load_monthly_snapshots, load_nav, load_research_prior,  # noqa: E402
                           load_state, load_trade_log, save_monthly_snapshots, save_state)

FUND_COLS = ["ticker", "market_cap", "sector", "roe", "pe", "pb", "ps", "net_income", "revenue",
             "rev_growth", "op_margin", "debt_to_equity", "div_yield"]
LABEL_COLS = ["fwd_1m", "fwd_3m", "fwd_ret", "real_ret", "xu_excess", "cpi_12m_pct",
              "hurdle_ret", "b_usd", "b_gold", "b_deposit", "beat_all"]


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


def resolve_labels(snaps: pd.DataFrame, wide, cpi, index_close, bm=None, crate=None) -> pd.DataFrame:
    """Fill 1m/3m/12m labels of past monthly snapshots once the windows have elapsed."""
    if snaps.empty:
        return snaps
    s = snaps.copy()
    for c in LABEL_COLS:
        if c not in s.columns:
            s[c] = np.nan
    need = s[s["fwd_ret"].isna() | s["fwd_3m"].isna() | s["fwd_1m"].isna()]["tarih"].unique()
    if len(need) and wide is not None:
        lab = forward_labels(wide, list(need), cpi, index_close, bm, crate)
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
    # multi-benchmark hurdle can also resolve later (CPI lag)
    m2 = s["fwd_ret"].notna() & s["hurdle_ret"].isna()
    if m2.any():
        for d in s.loc[m2, "tarih"].unique():
            w = BM.window_returns(bm, cpi, crate, d, pd.Timestamp(d) + pd.DateOffset(months=12))
            if np.isfinite(w["hurdle"]):
                mm = m2 & (s["tarih"] == d)
                s.loc[mm, "hurdle_ret"] = w["hurdle"]
                s.loc[mm, "b_usd"], s.loc[mm, "b_gold"], s.loc[mm, "b_deposit"] = w["usd"], w["gold"], w["deposit"]
                s.loc[mm, "beat_all"] = (s.loc[mm, "fwd_ret"] > w["hurdle"]).astype(float)
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


def active_strategy() -> dict:
    """Portfolio rules chosen by the walk-forward strategy lab (data/strategy_config.json)."""
    cfg = read_json(C.STRATEGY_CONFIG_FILE) or {}
    st = cfg.get("strategy") if isinstance(cfg, dict) else None
    return {**default_strategy(), **(st if isinstance(st, dict) else {})}


def gold_bar_today(bm, today):
    gb = LAB.gold_bars(bm)
    if gb is None:
        return None
    gb = gb[gb.index <= pd.Timestamp(today)]
    if len(gb) < 2 or not np.isfinite(gb["chg_pct"].iloc[-1]):
        return None
    return gb.iloc[[-1]].rename(index={gb.index[-1]: GOLD_TICKER})


def monthly_review(state, research, snap, today, index_close, cpi, cpi_stats, guard, hist_fn, as_of=None,
                   bm=None, crate=None):
    """`as_of` = last COMPLETED session (used by intraday refresh runs: no partial bars)."""
    pf = state["portfolio"]
    intraday = as_of is not None
    today = pd.Timestamp(as_of) if intraday else today
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
    hist = {t: g[g.index <= today] for t, g in hist.items()}
    hist = {t: g for t, g in hist.items() if len(g)}
    if len(hist) < C.MIN_CROSS_SECTION:
        return {"status": "HISTORY_UNAVAILABLE", "n_hist": len(hist)}, []
    wide = wide_from_history(hist)
    snaps = resolve_labels(snaps, wide, cpi, index_close, bm, crate)

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
    strat = active_strategy()
    eq_frac, gold_w = LAB.overlay_alloc(strat.get("overlay", "none"), today, bm, crate)
    if LAB.gold_bars(bm) is None:
        gold_w = 0.0                       # no gold price series -> no gold sleeve
    state["active_strategy"] = {**strat, "equity_frac": eq_frac, "gold_w": gold_w}
    chg = None if intraday else snap.set_index("ticker")["change_pct"]
    orders, summ = plan_rebalance(pf, frame, state, exposure, block=bool(guard.get("block_new_entries")),
                                  today_change=chg, params=strat, equity_frac=eq_frac, gold_w=gold_w)
    keep = [o for o in pf["pending"] if o.get("reason") == "CATASTROPHE_STOP"]
    pf["pending"] = keep + [o for o in orders if o["ticker"] not in {k["ticker"] for k in keep}]
    pf["last_rebalance_month"] = today.strftime("%Y-%m")

    sel = set(summ.get("holds", [])) | set(summ.get("buys", []))
    cols = ["ticker", "sector", "close_adj", "med_value_traded", "vol_ann_pct", "beta", "composite", "composite_pct",
            "exp_real_12m", "exp_nominal_12m", "hurdle_12m", "exp_over_hurdle", "p_beat_cpi", "p_beat_all",
            "regime_label", "model_version", "fund_break"] + \
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


def run(force: bool = False, today=None, fetch=MD.fetch_snapshot, hist_fn=MD.download_history,
        cpi_fn=INF.load_cpi, regime_fn=RM.download_regime_series, cash_fn=INF.load_cash_rate,
        bench_fn=BM.download_benchmarks, refresh: bool = False) -> dict:
    real_run = today is None
    today = pd.Timestamp(today) if today is not None else cal.today_tr()
    refresh = bool(refresh)
    if real_run and not force and not refresh:
        now_tr = pd.Timestamp.now(tz=C.MARKET_TZ)
        if now_tr.hour * 60 + now_tr.minute < 18 * 60 + 15 or not cal.is_session(today):
            refresh = True
            print(f"ℹ️ Kapanış verisi yok ({now_tr:%d.%m %H:%M}) → YENİLEME modu: TÜFE/nakit/rejim güncellenir, "
                  "gerekirse aylık gözden geçirme son tamamlanan seansa göre yenilenir; işlem/NAV kaydı yapılmaz.")
    if not refresh and not cal.is_session(today) and not force:
        print(f"ℹ️ {today.date()} BIST seansı değil.")
        return {"status": "NOT_SESSION"}
    as_of = None
    if refresh:
        d = today - pd.Timedelta(days=1)
        while not cal.is_session(d):
            d -= pd.Timedelta(days=1)
        as_of = d

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
        send_telegram(TG.blocked_report(today, str(quality.get("reason"))))
        return {"status": "DATA_BLOCKED"}
    fp = _fingerprint(snap)
    if refresh:
        fp = state.get("last_run", {}).get("fingerprint")      # a refresh never consumes the day
    elif not force and state.get("last_run", {}).get("fingerprint") == fp:
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
    RM.update_regime(state, snap, today, series=rser, allow_download=False)

    # inflation
    cpi, cmeta = cpi_fn()
    if cmeta.get("status") not in ("OK", "STALE") and rser is not None and "fx" in rser:
        px = INF.proxy_from_fx(rser["fx"])
        if len(px) >= 24:
            cpi, cmeta = px, {**cmeta, "source": "USDTRY_PROXY", "status": "PROXY", "last_month": str(px.index[-1].date())}
    cpi_stats = INF.inflation_stats(cpi, proxy=cmeta.get("status") == "PROXY")
    state["inflation"] = {**cpi_stats, **cmeta}

    # portfolio: execute pending orders at today's open, mark to close
    if not state.get("portfolio"):
        state["portfolio"] = new_portfolio(today)
    pf = state["portfolio"]
    # A review taken while inflation was unknown had no real-return gate: cancel its
    # not-yet-executed buys and redo the review now that an inflation estimate exists.
    lr = state.get("last_rebalance", {}) or {}
    if lr.get("status") == "OK" and lr.get("expected_inflation_12m") is None and cpi_stats.get("expected_12m_pct") is not None:
        before = len(pf["pending"])
        pf["pending"] = [o for o in pf["pending"] if o["action"] == "SELL"]
        pf["last_rebalance_month"] = None
        print(f"ℹ️ Enflasyon kapısı olmadan verilmiş {before - len(pf['pending'])} emir iptal edildi; aylık gözden geçirme yenileniyor.")
    crate, cr_meta = cash_fn()
    cash_y = INF.cash_yield_at(crate, today)
    state["cash_rate"] = {**cr_meta, "net_yield_pct": round(cash_y, 2)}
    # benchmarks (USD, gold, BIST100) and the forward-looking multi-benchmark hurdle
    try:
        bm = bench_fn()
    except Exception as exc:
        print(f"⚠️ Kıyas serileri alınamadı ({exc}); USDTRY rejim serisinden kullanılıyor, altın yok.")
        bm = BM.assemble(rser["fx"], None, rser["idx"]) if rser is not None else None
    state["hurdles"] = BM.expected_hurdles(bm, cpi_stats, cash_y if len(crate) else None, as_of=as_of or today)
    bench = {"bm": bm, "crate": crate}
    events, lots = [], []
    wts = pf_weights(pf)
    nav_df = load_nav()
    if not refresh:
        bars = snap.set_index("ticker")[["open", "close", "change_pct"]].rename(columns={"change_pct": "chg_pct"})
        gbar = gold_bar_today(bm, today)
        if gbar is not None:
            bars = pd.concat([bars, gbar])
        events, lots = apply_day(pf, bars, today, cash_yield_pct=cash_y)
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
    else:
        g0 = state.get("autonomy_guard", {}) or {}
        guard = {"mode": g0.get("mode", "WATCH"), "block_new_entries": bool(g0.get("block_new_entries", False))}

    review, top = None, []
    state["_no_inflation"] = cpi_stats.get("expected_12m_pct") is None
    review_month = (as_of or today).strftime("%Y-%m")
    if pf.get("last_rebalance_month") != review_month:
        review, top = monthly_review(state, research, snap, today, index_close, cpi, cpi_stats, guard, hist_fn, as_of=as_of,
                                     bm=bm, crate=crate)
        state["last_rebalance"] = {"date": str((as_of or today).date()), "mode": "REFRESH" if refresh else "EOD", **{k: v for k, v in review.items() if k not in ("weights",)}}

    lots_all = enrich_lots(load_trade_log(), cpi, index_close, bench)
    state["performance"] = {"nav": nav_metrics(nav_df, cpi, bench), "lots": lot_metrics(lots_all),
                            "open_positions": {t: {"w": round(wts.get(t, 0), 4), "ret_pct": round((p["level"] - 1) * 100, 2),
                                                   "since": p["entry_date"]} for t, p in pf["positions"].items()}}
    state.pop("_no_inflation", None)
    state["last_run"] = {"date": str(today.date()), "status": "OK", "fingerprint": fp, "mode": "REFRESH" if refresh else "EOD",
                         "monthly_review": bool(review), "utc": datetime.utcnow().isoformat() + "Z"}
    save_state(state)

    if review and review.get("status") == "OK":
        send_telegram(TG.monthly_report(as_of or today, state, review, top))
    elif review:
        send_telegram(f"⚠️ <b>BIST Reel Getiri</b>\nAylık gözden geçirme tamamlanamadı ({escape(str(review.get('status')))}). Bir sonraki çalışmada tekrar denenecek.")
        pf["last_rebalance_month"] = None
        save_state(state)
    elif events:
        nv = pd.to_numeric(nav_df["nav"], errors="coerce").dropna() if nav_df is not None and "nav" in nav_df else pd.Series(dtype=float)
        day_ret = float((nv.iloc[-1] / nv.iloc[-2] - 1) * 100) if len(nv) >= 2 else None
        send_telegram(TG.events_report(today, events, state, day_ret))
    print(f"✅ {today.date()} [{'YENİLEME' if refresh else 'EOD'}] | NAV {pf['nav']:.4f} | pozisyon {len(pf['positions'])} | aylık={'evet' if review else 'hayır'}")
    return {"status": "OK", "state": state, "events": events, "review": review}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="TÜFE/nakit/rejim güncelle, işlem yapma")
    a = ap.parse_args()
    if a.self_test:
        from selftest import run_self_test
        sys.exit(0 if run_self_test() else 1)
    run(force=a.force, refresh=a.refresh)


if __name__ == "__main__":
    main()
