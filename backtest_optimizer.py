"""Walk-forward research backtest for the V3 real-return engine (free data only).

* Adjusted daily OHLCV (yfinance) for a broad liquid BIST universe since 2012,
  XU100 + USDTRY for the regime model, Turkish CPI for real returns,
  point-in-time fundamentals from Is Yatirim (best effort, lagged 75/100 days).
* Monthly decisions use EXACTLY the live functions (factors.price_factors_at,
  meta_engine.score_universe / plan_rebalance, portfolio.apply_day).
* Walk-forward by year: factor weights are fitted only on months whose 12-month
  labels were fully known before the test year (purged); calibration uses only
  earlier OUT-OF-SAMPLE months; the HMM is fitted on data before the test year.
* Output: data/research_prior_v3.json (prior + OOS calibration for the live
  learner) and data/backtest_report_v3.json (real/CPI and XU100 metrics).
Known limitations are written into the report (survivorship, no dividends in
cash, fundamentals coverage).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import config as C
import fundamentals_hist as FH
import inflation as INF
import market_data as MD
import regime_model as RM
import benchmarks as BM
from backtest_validator import enrich_lots, lot_metrics, nav_metrics
from calibration import add_excess, bucket_table, calibrate, cutoff_stats, market_stats
from factors import build_frame, composite, price_factors_at, wide_from_history
from labels import forward_labels, month_start_sessions
from learner_engine import daily_rank_ic, factor_corr, fit_weights, get_prior, newey_west
from meta_engine import exposure_from, plan_rebalance, score_universe
from portfolio import apply_day, new_portfolio, weights as pf_weights
from state_manager import atomic_json_write, load_state

UNIVERSE = [
    "AEFES", "AGHOL", "AKBNK", "AKCNS", "AKSA", "AKSEN", "ALARK", "ALBRK", "ALFAS", "ANSGR", "ARCLK", "ASELS",
    "ASTOR", "AYGAZ", "BAGFS", "BERA", "BIMAS", "BRISA", "BRSAN", "BRYAT", "BUCIM", "CANTE", "CCOLA", "CEMTS",
    "CIMSA", "DEVA", "DOAS", "DOHOL", "ECILC", "EGEEN", "EKGYO", "ENJSA", "ENKAI", "EREGL", "EUPWR", "FROTO",
    "GARAN", "GESAN", "GLYHO", "GOZDE", "GUBRF", "HALKB", "HEKTS", "INDES", "IPEKE", "ISCTR", "ISFIN", "ISGYO",
    "ISMEN", "KARSN", "KARTN", "KCAER", "KCHOL", "KONTR", "KONYA", "KORDS", "KOZAA", "KOZAL", "KRDMD", "LOGO",
    "MAVI", "MGROS", "MIATK", "NETAS", "NTHOL", "ODAS", "OTKAR", "OYAKC", "PARSN", "PETKM", "PGSUS", "PRKME",
    "QUAGR", "SAHOL", "SARKY", "SASA", "SELEC", "SISE", "SKBNK", "SMRTG", "SNGYO", "SOKM", "TATGD", "TAVHL",
    "TCELL", "THYAO", "TKFEN", "TMSN", "TOASO", "TRGYO", "TSKB", "TTKOM", "TTRAK", "TUKAS", "TUPRS", "TURSG",
    "ULKER", "VAKBN", "VERUS", "VESBE", "VESTL", "YATAS", "YEOTK", "YKBNK", "ZOREN", "ZRGYO",
]
MIN_TRAIN_MONTHS = 36


def fund_inputs_at(pit: pd.DataFrame, date, price_f: pd.DataFrame, latest_paid: Dict[str, float]) -> pd.DataFrame:
    if pit is None or pit.empty or price_f.empty:
        return pd.DataFrame(columns=["ticker"])
    d = FH.point_in_time(pit, date, price_f["ticker"].tolist())
    if d.empty:
        return pd.DataFrame(columns=["ticker"])
    px = price_f.set_index("ticker")["close_adj"]
    out = pd.DataFrame({"ticker": d["ticker"].to_numpy()})
    eq = d["equity"].to_numpy(float)
    ni = d["net_income_ttm"].to_numpy(float)
    rev = d["revenue_ttm"].to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        shares = out["ticker"].map(latest_paid).to_numpy(float)
        out["market_cap"] = out["ticker"].map(px).to_numpy(float) * shares
        out["net_income"] = ni
        out["equity"] = eq
        out["revenue"] = rev
        out["roe"] = np.where(eq > 0, ni / eq * 100.0, np.nan)
        out["op_margin"] = np.where(rev > 0, d["op_profit_ttm"].to_numpy(float) / rev * 100.0, np.nan)
        out["debt_to_equity"] = np.where(eq > 0, d["fin_debt"].to_numpy(float) / eq, np.nan)
        out["rev_growth"] = d["rev_growth_pct"].to_numpy(float)
    return out.replace([np.inf, -np.inf], np.nan)


def cpi_stats_at(cpi: pd.Series, date, proxy: bool = False) -> Dict:
    if cpi is None or cpi.empty:
        return {"yoy_pct": None, "expected_12m_pct": None}
    last_pub = pd.Timestamp(date) - pd.DateOffset(months=1)        # ~1 month publication lag
    s = cpi[cpi.index <= pd.Timestamp(last_pub.year, last_pub.month, 1)]
    return INF.inflation_stats(s, proxy=proxy)


def run(start: str = "2012-01-01", end: Optional[str] = None, save: bool = True,
        data: Optional[Dict] = None, regime_df: Optional[pd.DataFrame] = None,
        cpi: Optional[pd.Series] = None, pit: Optional[pd.DataFrame] = None,
        bm: Optional[pd.DataFrame] = None, crate: Optional[pd.Series] = None,
        variants: Optional[List[Dict]] = None) -> Dict:
    state = load_state()
    sector_map = state.get("sector_map", {})
    data = data if data is not None else MD.download_history(UNIVERSE, start, end, min_rows=300)
    if len(data) < C.MIN_CROSS_SECTION:
        raise RuntimeError(f"Gerçek veri yetersiz: {len(data)} hisse")
    rdf = None
    try:
        rdf = regime_df if regime_df is not None else RM.download_regime_series(start=start, end=end)
        index_close, X = rdf["idx"], RM.make_features(rdf)
    except Exception as exc:
        print(f"⚠️ Endeks/rejim serisi yok: {exc}")
        index_close, X = None, None
    cpi_meta = {"source": "given"}
    if cpi is None:
        cpi, cpi_meta = INF.load_cpi_or_proxy(fx=rdf["fx"] if rdf is not None else None)
        if cpi_meta.get("status") == "PROXY":
            print("⚠️ Resmî TÜFE alınamadı; USDTRY vekili kullanılıyor (EVDS_API_KEY ekleyin).")
    cr_meta = {"source": "given"}
    if crate is None:
        crate, cr_meta = INF.load_cash_rate()
    if bm is None:
        try:
            bm = BM.download_benchmarks(start=str(int(start[:4]) - 1) + "-01-01", end=end)
        except Exception as exc:
            print(f"⚠️ Kıyas serileri alınamadı ({exc}); USDTRY rejim serisinden, altın yok.")
            bm = BM.assemble(rdf["fx"], None, rdf["idx"]) if rdf is not None else None
    bench = {"bm": bm, "crate": crate}
    fund_cov = 0.0
    if pit is None:
        try:
            pit = FH.load_history(list(data.keys()), int(start[:4]))
        except Exception as exc:
            print(f"⚠️ Temel veri geçmişi alınamadı: {exc}")
            pit = pd.DataFrame()
    latest_paid = {}
    if pit is not None and not pit.empty and "paid_in" in pit:
        lp = pit.dropna(subset=["paid_in"]).sort_values("period_end").groupby("ticker")["paid_in"].last()
        latest_paid = lp.to_dict()

    wide = wide_from_history(data)
    idx = wide["close"].index
    reb = month_start_sessions(idx[C.MIN_HISTORY_SESSIONS:])
    inputs, zrows = {}, []
    for d in reb:
        pf_ = price_factors_at(wide, index_close, d)
        if len(pf_) < C.MIN_CROSS_SECTION:
            continue
        fi = fund_inputs_at(pit, d, pf_, latest_paid)
        cs = cpi_stats_at(cpi, d, proxy=cpi_meta.get("status") == "PROXY")
        inputs[d] = (pf_, fi, cs)
        fr, cov = build_frame(pf_, fi, cs.get("yoy_pct"), sector_map)
        fund_cov = max(fund_cov, float(np.mean([cov.get(k, 0) for k in C.FUNDAMENTAL_FACTORS])))
        zrows.append(fr[["tarih", "ticker"] + [f"z_{k}" for k in C.FACTORS]])
    dates = sorted(inputs.keys())
    if len(dates) < MIN_TRAIN_MONTHS + 14:
        raise RuntimeError(f"Walk-forward için yetersiz ay: {len(dates)}")
    Z = pd.concat(zrows, ignore_index=True)
    lab = forward_labels(wide, dates, cpi, index_close, bm, crate)
    ds = Z.merge(lab, on=["tarih", "ticker"], how="inner")
    hand = get_prior(None)

    # ------------------------------------------------ walk-forward folds (yearly)
    first_test_i = MIN_TRAIN_MONTHS + 13
    test_starts = dates[first_test_i::12]
    folds, oos_rows = [], []
    fold_of_date = {}
    for k, T0 in enumerate(test_starts):
        T1 = test_starts[k + 1] if k + 1 < len(test_starts) else None
        train_dates = [d for d in dates if d + pd.DateOffset(months=13) <= T0]
        w, meta = fit_weights(ds[ds["tarih"].isin(train_dates)], hand)
        params = None
        if X is not None and len(X[X.index < T0]) > 500:
            params = RM.fit_hmm(X[X.index < T0])
        for d in dates:
            if d >= T0 and (T1 is None or d < T1):
                fold_of_date[d] = (k, w, params)
        folds.append({"test_start": str(T0.date()), "train_months": len(train_dates), "weights": w,
                      "factor_ic_train": {f: meta["factor_stats"][f]["ic_mean"] for f in C.FACTORS}})

    # ---------------- scoring pass: one scored cross-section per rebalance date (strategy-independent)
    alpha_cache = {}
    sim_days = idx[(idx >= dates[first_test_i])]
    O, Cl = wide["open"], wide["close"]
    chg = Cl.pct_change(fill_method=None) * 100.0
    cal_state = {"calibration": {}}
    frames = {}
    for day in sorted(fold_of_date.keys()):
        k, w, params = fold_of_date[day]
        if params is not None:
            if k not in alpha_cache:
                alpha_cache[k] = RM.filtered_probs(params, X)
            pos = X.index.searchsorted(day, side="right") - 1
            reg = RM.summarize(params, alpha_cache[k][: pos + 1], X.index[: pos + 1])
            reg["degraded"] = False
        else:
            reg = {"label": "UNKNOWN", "probs": {}, "p_risk_off": 0.5, "degraded": True, "exp_mkt_12m_pct": None}
        known = [r for r in oos_rows if r["tarih"].iloc[0] + pd.DateOffset(months=13) <= day]
        if known:
            kd = pd.concat(known, ignore_index=True).merge(lab, on=["tarih", "ticker"], how="inner")
            calibrate(cal_state, kd, None)
        pf_, fi, cs = inputs[day]
        st = {"model": {"champion_weights": w, "champion_version": f"wf{k}", "regime_weights": {}},
              "calibration": dict(cal_state["calibration"]), "regime": reg,
              "_no_inflation": cs.get("expected_12m_pct") is None,
              "hurdles": BM.expected_hurdles(bm, cs, INF.cash_yield_at(crate, day) if len(crate) else None, as_of=day)}
        frame, info = score_universe(pf_, fi, st, cs, sector_map)
        if frame.empty:
            continue
        oos_rows.append(frame[["tarih", "ticker", "composite", "composite_pct", "regime_label"]].assign(tarih=day))
        frames[day] = (frame, st, reg)

    # ---------------- strategy laboratory (walk-forward selected portfolio rules)
    import strategy_lab as LAB
    from meta_engine import default_strategy
    ctx = {"days": list(sim_days), "frames": frames, "bm": bm, "crate": crate,
           "bars": {d: pd.DataFrame({"open": O.loc[d], "close": Cl.loc[d], "chg_pct": chg.loc[d]}).dropna() for d in sim_days},
           "gold_bars": LAB.gold_bars(bm),
           "cash_y": (lambda d: INF.cash_yield_at(crate, d)),
           "xu": (lambda d: float(index_close.asof(d)) if index_close is not None else np.nan)}
    print(f"🧪 Strateji laboratuvarı başlıyor ({len(LAB.variant_grid()) + 1} varyant)...")
    lab_res = LAB.run_lab(ctx, cpi, bench, default_strategy(), variants=variants)
    sel = lab_res["selected"]
    sel_res = lab_res["results"][sel["name"]]
    def_res = lab_res["results"][lab_res["default_name"]]
    # headline = what goes live, measured out-of-sample: the walk-forward meta strategy if the lab
    # pick is adopted, otherwise the default rules (themselves never tuned on the test years)
    nav_df = lab_res["meta_nav"] if lab_res["adopted"] else def_res["nav"]
    lots_df = enrich_lots(pd.DataFrame(sel_res["lots"]), cpi, index_close, bench)
    open_now = sel_res["open"]

    # OOS IC of the composite (12m) per fold
    oos = pd.concat(oos_rows, ignore_index=True) if oos_rows else pd.DataFrame()
    oos_l = oos.merge(lab, on=["tarih", "ticker"], how="inner") if not oos.empty else pd.DataFrame()
    ic12 = daily_rank_ic(oos_l.dropna(subset=["fwd_ret"]), ["composite"], "fwd_ret") if not oos_l.empty else pd.DataFrame()
    m, se, t, n = newey_west(ic12["composite"], 11) if not ic12.empty else (0.0, 0, 0.0, 0)

    # per-year table
    per_year = {}
    if not nav_df.empty:
        y = nav_df.set_index("tarih")
        for yr, g in y.groupby(y.index.year):
            if len(g) < 20:
                continue
            nom = (g["nav"].iloc[-1] / g["nav"].iloc[0] - 1) * 100
            c = INF.cpi_ratio(cpi, g.index[0], g.index[-1])
            xr = (g["xu100"].iloc[-1] / g["xu100"].iloc[0] - 1) * 100 if g["xu100"].notna().all() else np.nan
            per_year[int(yr)] = {"nominal_pct": round(float(nom), 2),
                                 "cpi_pct": round(float((c - 1) * 100), 2) if np.isfinite(c) else None,
                                 "real_pct": round(float(((1 + nom / 100) / c - 1) * 100), 2) if np.isfinite(c) else None,
                                 "xu100_pct": round(float(xr), 2) if np.isfinite(xr) else None}
            w = BM.window_returns(bm, cpi, crate, g.index[0], g.index[-1])
            per_year[int(yr)].update({"usd_pct": None if not np.isfinite(w["usd"]) else round(float(w["usd"]), 2),
                                      "gold_pct": None if not np.isfinite(w["gold"]) else round(float(w["gold"]), 2),
                                      "deposit_pct": None if not np.isfinite(w["deposit"]) else round(float(w["deposit"]), 2),
                                      "hurdle_pct": None if not np.isfinite(w["hurdle"]) else round(float(w["hurdle"]), 2),
                                      "beat_all": None if not np.isfinite(w["hurdle"]) else bool(nom > w["hurdle"])})

    prior = build_research_prior(ds, X, oos_l)
    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z", "engine_version": C.ENGINE_VERSION,
        "hurdle_mode": getattr(C, "HURDLE_MODE", "max"), "hurdle_edge_pct": C.MIN_EDGE_OVER_HURDLE_PCT,
        "objective": {"horizon_months": C.HORIZON_MONTHS,
                      "primary": "beat ALL of " + ", ".join(C.HURDLE_COMPONENTS) + f" (USD incl. {C.US_INFLATION_PCT}% US inflation)",
                      "secondary": "excess vs XU100"},
        "period": f"{str(pd.Timestamp(dates[first_test_i]).date())}..{str(pd.Timestamp(idx[-1]).date())}",
        "universe_downloaded": len(data), "rebalance_months": len(dates),
        "cpi_source": cpi_meta, "cash_rate_source": cr_meta, "fundamentals_coverage": round(fund_cov, 3),
        "data_source": "Yahoo Finance adjusted OHLCV + Is Yatirim statements + CPI (EVDS/FRED)",
        "synthetic_data_used": False, "cost_round_trip_pct": C.COST_ROUND_TRIP_PCT,
        "rules": {"target_positions": C.TARGET_POSITIONS, "buy_pct": C.BUY_PCT, "hold_pct": C.HOLD_PCT,
                  "min_expected_real_pct": C.MIN_EXPECTED_REAL_PCT,
                  "drawdown_flag_peak_pct": C.CATASTROPHE_FROM_PEAK_PCT, "drawdown_flag_entry_pct": C.CATASTROPHE_FROM_ENTRY_PCT,
                  "hard_stop_entry_pct": C.HARD_STOP_FROM_ENTRY_PCT, "exposure_by_mode": C.EXPOSURE_BY_MODE},
        "portfolio": nav_metrics(nav_df, cpi, bench),
        "portfolio_basis": ("walk-forward META strategy (variant chosen each year using only earlier data)"
                            if lab_res["adopted"] else "default rules (lab selection did not beat them out-of-sample)"),
        "portfolio_default_rules": nav_metrics(def_res["nav"], cpi, bench),
        "portfolio_selected_insample": nav_metrics(sel_res["nav"], cpi, bench),
        "closed_lots": lot_metrics(lots_df),
        "strategy_lab": {"variants_tested": lab_res["variants_tested"], "selected": sel,
                         "lab_best": lab_res["lab_best"], "adopted": lab_res["adopted"],
                         "meta_oos_score": lab_res["meta_oos_score"], "default_oos_score": lab_res["default_oos_score"],
                         "selected_score": round(float(lab_res["selected_score"]), 2),
                         "choices_by_year": lab_res["choices_by_year"],
                         "meta_beat_hurdle_pct": lab_res["meta_beat_hurdle_pct"],
                         "meta_median_excess_pp": lab_res["meta_median_excess_pp"],
                         "top10": lab_res["table"][:10],
                         "default": next((r for r in lab_res["table"] if r["name"] == lab_res["default_name"]), None)},
        "open_positions_end": open_now,
        "oos_composite_ic_12m": {"mean": round(float(m), 4), "t_nw": round(float(t), 2), "n_months": int(n)},
        "per_year": per_year,
        "folds": folds,
        "factor_ic_full_sample_12m": prior.get("ic_mean"),
        "factor_t_full_sample_12m": prior.get("ic_t_nw"),
        "limitations": ["survivorship: universe = today's liquid names",
                        "dividends are in adjusted prices (reinvested); idle cash earns TCMB funding rate - 2pp, after 15% tax"
                        if len(crate) else "dividends are in adjusted prices (reinvested); idle cash earns 0 (rate series unavailable)",
                        f"fundamentals coverage {round(fund_cov, 2)} (Is Yatirim best effort)",
                        "autonomy guard neutral in backtest"],
    }
    print(json.dumps({k: report[k] for k in ("period", "portfolio", "strategy_lab", "oos_composite_ic_12m", "per_year")},
                     ensure_ascii=False, indent=2, default=str))
    if save:
        atomic_json_write(C.RESEARCH_PRIOR_FILE, prior)
        atomic_json_write(C.BACKTEST_REPORT_FILE, report)
        atomic_json_write(C.STRATEGY_CONFIG_FILE, {
            "generated_at": report["generated_at"], "strategy": sel,
            "score": report["strategy_lab"]["selected_score"],
            "why": "walk-forward strategy lab: best 0.5*P25+0.5*median of rolling 12m excess over the hurdle",
            "meta_oos": {k: report["portfolio"].get(k) for k in ("cagr_pct", "real_cagr_pct", "xu100_cagr_pct",
                                                                   "max_drawdown_pct", "rolling12m_beat_all_pct")}})
    return {"report": report, "prior": prior, "nav": nav_df, "lots": lots_df}


def build_research_prior(ds: pd.DataFrame, X: Optional[pd.DataFrame], oos_l: pd.DataFrame) -> Dict:
    zc = [f"z_{k}" for k in C.FACTORS]
    d = ds.dropna(subset=["fwd_ret"])
    ic = daily_rank_ic(d, zc, "fwd_ret")
    ic_mean, ic_t = {}, {}
    for k in C.FACTORS:
        s = ic.get(f"z_{k}", pd.Series(dtype=float)).dropna()
        mm, se, tt, n = newey_west(s, 11) if len(s) else (0.0, 0, 0.0, 0)
        ic_mean[k], ic_t[k] = round(float(mm), 5), round(float(tt), 3)
    om = factor_corr(d)
    prior = {"generated_at": datetime.utcnow().strftime("%Y-%m-%d"), "horizon_months": C.HORIZON_MONTHS,
             "ic_mean": ic_mean, "ic_t_nw": ic_t, "omega": np.round(om, 4).tolist() if om is not None else None,
             "n_dates": int(len(ic)), "n_eff_dates": round(len(ic) / C.LABEL_HORIZON, 1), "regime_ic": {}}
    if X is not None and len(X) > 500:
        params = RM.fit_hmm(X)
        a = RM.filtered_probs(params, X)
        labs = pd.Series([params["labels"][int(i)] for i in a.argmax(axis=1)], index=X.index)
        d2 = d.copy()
        d2["regime_label"] = d2["tarih"].map(lambda t: labs.asof(t) if t >= labs.index[0] else None)
        for L, sub in d2.dropna(subset=["regime_label"]).groupby("regime_label"):
            icr = daily_rank_ic(sub, zc, "fwd_ret")
            if len(icr) < 24:
                continue
            prior["regime_ic"][L] = {"ic_mean": {k: round(float(icr[f"z_{k}"].mean()), 5) if icr[f"z_{k}"].notna().any() else 0.0
                                                 for k in C.FACTORS},
                                     "n_dates": int(len(icr)), "n_eff_dates": round(len(icr) / C.LABEL_HORIZON, 1)}
    if oos_l is not None and not oos_l.empty:
        e = add_excess(oos_l)
        if not e.empty:
            prior["calibration"] = {"buckets": bucket_table(e), "cutoffs": cutoff_stats(e), "market": market_stats(e),
                                    "source": "walk_forward_oos"}
    return prior


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-date", default="2012-01-01")
    ap.add_argument("--end-date", default=None)
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    run(a.start_date, a.end_date, save=not a.no_save)


if __name__ == "__main__":
    main()
