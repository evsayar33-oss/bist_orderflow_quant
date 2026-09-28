"""Walk-forward research backtest = the LIVE code path on historical data.

What it does (monthly GitHub Action, free data via yfinance):
  1. Downloads split/dividend-ADJUSTED daily OHLCV for a broad liquid BIST universe.
  2. Rebuilds, for every historical session, a snapshot with the SAME fields and
     definitions the live engine gets from TradingView (rvol, perf_w/1m/3m,
     1-month range, ATR ...), then scores it with meta_engine.score_snapshot -
     the exact live function.
  3. Walk-forward: expanding train window, purged split, weights fitted on the
     first part of train, score->return calibration on the purged last part
     (out-of-sample), HMM regime fitted on train only and FILTERED forward.
  4. Trades follow labels.barrier_step (same as the live ledger) with the live
     portfolio caps; portfolio P&L is marked to market DAILY in date order,
     after costs, so drawdown is real.
  5. Writes data/research_prior.json (factor IC prior, factor correlation,
     regime-conditional IC, OOS calibration) that the live learner uses as its
     Bayesian prior, plus data/backtest_report.json.

Known limitations (reported, not hidden): the universe is today's liquid names
(survivorship bias), yfinance can miss tickers, sector-neutralisation uses the
sector map collected by the live engine when available.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import config as C
import regime_model as RM
from backtest_validator import portfolio_metrics, stability, trade_metrics
from calibration import add_excess, bucket_table, calibrate, cutoff_stats
from flow_engine import build_factor_frame, composite
from labels import compute_labels
from learner_engine import daily_rank_ic, factor_corr, fit_weights, get_prior, newey_west
from meta_engine import score_snapshot
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

MIN_TRAIN_SESSIONS = 504
TEST_SESSIONS = 126


# ------------------------------------------------------------------ data
def download_ohlcv(tickers: List[str], start: str, end: Optional[str]) -> Dict[str, pd.DataFrame]:
    import yfinance as yf
    out: Dict[str, pd.DataFrame] = {}
    syms = [t + ".IS" for t in tickers]
    for i in range(0, len(syms), 35):
        chunk = syms[i:i + 35]
        raw = None
        for _ in range(2):
            try:
                raw = yf.download(chunk, start=start, end=end, interval="1d", auto_adjust=True,
                                  group_by="ticker", progress=False, threads=True)
                break
            except Exception as exc:  # pragma: no cover
                print(f"⚠️ yfinance chunk hatası: {exc}")
        if raw is None or raw.empty:
            continue
        for s in chunk:
            try:
                g = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
                g = g.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
                g = g[(g["close"] > 0) & (g["volume"] >= 0)]
                g.index = pd.to_datetime(g.index).tz_localize(None).normalize()
                if len(g) >= 300:
                    out[s.replace(".IS", "")] = g
            except Exception:
                continue
    if len(out) < C.MIN_CROSS_SECTION:
        raise RuntimeError(f"Gerçek veri kapsamı yetersiz: {len(out)} hisse (min {C.MIN_CROSS_SECTION}).")
    return out


def snapshot_fields(g: pd.DataFrame) -> pd.DataFrame:
    """Same field definitions the live engine receives from TradingView."""
    f = pd.DataFrame(index=g.index)
    f["open"], f["high"], f["low"], f["close"], f["volume"] = g["open"], g["high"], g["low"], g["close"], g["volume"]
    f["change_pct"] = g["close"].pct_change() * 100.0
    f["value_traded"] = g["close"] * g["volume"]
    f["rvol"] = g["volume"] / g["volume"].shift(1).rolling(10).mean()
    f["perf_w"] = g["close"].pct_change(5) * 100.0
    f["perf_1m"] = g["close"].pct_change(21) * 100.0
    f["perf_3m"] = g["close"].pct_change(63) * 100.0
    f["high_1m"] = g["high"].rolling(21).max()
    f["low_1m"] = g["low"].rolling(21).min()
    tr = pd.concat([g["high"] - g["low"], (g["high"] - g["close"].shift()).abs(),
                    (g["low"] - g["close"].shift()).abs()], axis=1).max(axis=1)
    f["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    return f.replace([np.inf, -np.inf], np.nan)


def build_inputs(data: Dict[str, pd.DataFrame], sector_map: Dict[str, str]):
    frames = []
    for t, g in data.items():
        f = snapshot_fields(g)
        f["ticker"] = t
        f["sector"] = sector_map.get(t)
        f["tarih"] = f.index
        frames.append(f.dropna(subset=["perf_3m", "atr", "rvol", "high_1m"]))
    long = pd.concat(frames, ignore_index=True)
    counts = long.groupby("tarih")["ticker"].size()
    good = counts[counts >= C.MIN_CROSS_SECTION].index
    long = long[long["tarih"].isin(good)]
    snaps = {d: g.reset_index(drop=True) for d, g in long.groupby("tarih")}
    dates = sorted(snaps.keys())
    close = pd.DataFrame({t: g["close"] for t, g in data.items()}).sort_index()
    close = close.reindex(sorted(set(close.index)))
    panel = {
        "dates": list(close.index),
        "open": pd.DataFrame({t: g["open"] for t, g in data.items()}).reindex(close.index),
        "high": pd.DataFrame({t: g["high"] for t, g in data.items()}).reindex(close.index),
        "low": pd.DataFrame({t: g["low"] for t, g in data.items()}).reindex(close.index),
        "close": close,
    }
    atr = pd.DataFrame({t: snapshot_fields(g)["atr"] for t, g in data.items()}).reindex(close.index)
    panel["atr_pct"] = atr / close * 100.0
    panel["breaks"] = pd.DataFrame(False, index=close.index, columns=close.columns)
    panel["adjacent"] = np.ones(len(close.index), bool)
    return snaps, dates, panel


def factor_dataset(snaps: Dict, dates: List, labels: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for d in dates:
        fr, _ = build_factor_frame(snaps[d])
        rows.append(fr[["tarih", "ticker"] + [f"z_{k}" for k in C.FACTORS]])
    z = pd.concat(rows, ignore_index=True)
    z["tarih"] = pd.to_datetime(z["tarih"]).dt.normalize()
    return z.merge(labels, on=["tarih", "ticker"], how="inner")


# ------------------------------------------------------------------ regime helpers
def regime_at(params: Dict, alpha: np.ndarray, xdates: pd.DatetimeIndex, d) -> Dict:
    pos = xdates.searchsorted(pd.Timestamp(d), side="right") - 1
    if pos < 0:
        return {"label": "UNKNOWN", "probs": {}, "p_risk_off": 0.5, "exp_mkt_5d_pct": 0.0, "degraded": True}
    s = RM.summarize(params, alpha[: pos + 1], xdates[: pos + 1])
    s["degraded"] = False
    return s


# ------------------------------------------------------------------ walk-forward
def run_walk_forward(snaps, dates, panel, ds, X: Optional[pd.DataFrame]) -> Dict:
    hand = get_prior(None)
    date_idx = {d: i for i, d in enumerate(panel["dates"])}
    close = panel["close"]
    lab_idx = ds.set_index(["tarih", "ticker"])
    trades, oos_rows, folds, fold_trade_metrics = [], [], [], []
    ic_oos = []
    start = MIN_TRAIN_SESSIONS
    purge = C.MAX_HOLD + 1
    while start < len(dates):
        test_dates = dates[start:start + TEST_SESSIONS]
        train_dates = dates[: max(0, start - purge)]
        n_w = int(len(train_dates) * 0.7)
        w_dates = train_dates[: max(0, n_w - purge)]
        c_dates = train_dates[n_w:]
        w_cal, _ = fit_weights(ds[ds["tarih"].isin(w_dates)], hand)
        cal_rows = ds[ds["tarih"].isin(c_dates)].copy()
        cal_rows["composite"] = composite(cal_rows, w_cal)
        cal_rows["composite_pct"] = cal_rows.groupby("tarih")["composite"].rank(pct=True) * 100.0
        st_cal = {"calibration": {}}
        calibrate(st_cal, cal_rows, None)
        w_fold, meta = fit_weights(ds[ds["tarih"].isin(train_dates)], hand)

        params, alpha, xdates = None, None, None
        if X is not None and len(X[X.index <= train_dates[-1]]) > 300:
            params = RM.fit_hmm(X[X.index <= train_dates[-1]])
            alpha = RM.filtered_probs(params, X)
            xdates = X.index

        open_pos: List[Dict] = []
        fold_trades = []
        for d in test_dates:
            i = date_idx[d]
            open_pos = [p for p in open_pos if p["exit_idx"] > i]
            reg = regime_at(params, alpha, xdates, d) if params else {
                "label": "UNKNOWN", "probs": {}, "p_risk_off": 0.5, "exp_mkt_5d_pct": 0.0, "degraded": True}
            st = {"model": {"champion_weights": w_fold, "champion_version": f"wf{len(folds)}", "regime_weights": {}},
                  "calibration": st_cal["calibration"], "regime": reg}
            scored, info = score_snapshot(snaps[d], st, None, {"exposure_multiplier": 1.0},
                                          open_positions=len(open_pos),
                                          held_tickers={p["ticker"] for p in open_pos})
            oos_rows.append(scored[["tarih", "ticker", "composite", "composite_pct"]].assign(tarih=d))
            for _, r in scored[scored["eligible"]].iterrows():
                key = (d, r["ticker"])
                if key not in lab_idx.index:
                    continue
                lb = lab_idx.loc[key]
                if isinstance(lb, pd.DataFrame):
                    lb = lb.iloc[0]
                if lb["filled"] < 1 or not np.isfinite(lb["barrier_gross"]) or lb["exit_k"] <= 0:
                    continue
                tr = {"signal_date": d, "ticker": r["ticker"], "size_pct": float(r["size_pct"]),
                      "entry_idx": i + 1, "exit_idx": i + int(lb["exit_k"]), "entry_px": float(lb["entry_px"]),
                      "exit_px": float(lb["exit_px"]), "exit_reason": lb["exit_reason"],
                      "gross_ret_pct": float(lb["barrier_gross"]), "net_ret_pct": float(lb["barrier_net"]),
                      "exp_net_pct": float(r["exp_net_pct"]), "regime": reg.get("label"), "fold": len(folds)}
                open_pos.append(tr)
                fold_trades.append(tr)
        trades.extend(fold_trades)
        tdf = pd.DataFrame(fold_trades)
        fm = trade_metrics(tdf) if not tdf.empty else {"trades": 0}
        fold_trade_metrics.append(fm)
        test_ds = ds[ds["tarih"].isin(test_dates)].copy()
        test_ds["_c"] = composite(test_ds, w_fold)
        ic = daily_rank_ic(test_ds, ["_c"], "fwd_ret")
        if not ic.empty:
            ic_oos.append(ic["_c"])
        folds.append({"train_end": str(pd.Timestamp(train_dates[-1]).date()),
                      "test_start": str(pd.Timestamp(test_dates[0]).date()),
                      "test_end": str(pd.Timestamp(test_dates[-1]).date()),
                      "weights": w_fold, "pct_cutoff": st_cal["calibration"].get("pct_cutoff"),
                      "oos_ic_mean": round(float(ic["_c"].mean()), 4) if not ic.empty else None,
                      "trades": fm})
        start += TEST_SESSIONS

    tdf = pd.DataFrame(trades)
    # daily mark-to-market portfolio, date ordered, costs charged at exit
    pr = pd.Series(0.0, index=close.index)
    if not tdf.empty:
        cost = C.COST_ROUND_TRIP_PCT / 100.0
        cvals = close.to_numpy(float)
        cols = {t: j for j, t in enumerate(close.columns)}
        for tr in trades:
            j, e, x, w = cols[tr["ticker"]], tr["entry_idx"], tr["exit_idx"], tr["size_pct"] / 100.0
            prev = tr["entry_px"]
            for t in range(e, x + 1):
                px = tr["exit_px"] if t == x else cvals[t, j]
                if not np.isfinite(px):
                    continue
                pr.iat[t] += w * (px / prev - 1.0)
                prev = px
            pr.iat[x] -= w * cost
    first = dates[MIN_TRAIN_SESSIONS] if len(dates) > MIN_TRAIN_SESSIONS else dates[0]
    pr = pr[pr.index >= first]
    ic_all = pd.concat(ic_oos) if ic_oos else pd.Series(dtype=float)
    m, se, t, n = newey_west(ic_all, C.LABEL_HORIZON - 1) if len(ic_all) else (0, 0, 0, 0)
    per_year = {}
    if not tdf.empty:
        tdf["year"] = pd.to_datetime(tdf["signal_date"]).dt.year
        per_year = {int(y): trade_metrics(g) for y, g in tdf.groupby("year")}
    oos = pd.concat(oos_rows, ignore_index=True) if oos_rows else pd.DataFrame()
    return {"trades": tdf, "portfolio_daily": pr, "folds": folds, "fold_trade_metrics": fold_trade_metrics,
            "oos_ic": {"mean": round(float(m), 5), "t_nw": round(float(t), 3), "n_dates": int(n)},
            "per_year": per_year, "oos_scores": oos}


def build_research_prior(ds: pd.DataFrame, X: Optional[pd.DataFrame], oos_scores: pd.DataFrame) -> Dict:
    zc = [f"z_{k}" for k in C.FACTORS]
    ic = daily_rank_ic(ds, zc, "fwd_ret")
    ic_mean, ic_t = {}, {}
    for k in C.FACTORS:
        s = ic.get(f"z_{k}", pd.Series(dtype=float))
        m, se, t, n = newey_west(s, C.LABEL_HORIZON - 1) if len(s.dropna()) else (0.0, 0, 0.0, 0)
        ic_mean[k], ic_t[k] = round(float(m), 5), round(float(t), 3)
    omega = factor_corr(ds)
    prior = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d"),
        "ic_mean": ic_mean, "ic_t_nw": ic_t,
        "omega": np.round(omega, 4).tolist() if omega is not None else None,
        "n_dates": int(len(ic)), "n_eff_dates": round(len(ic) / C.LABEL_HORIZON, 1),
        "regime_ic": {},
    }
    if X is not None and len(X) > 300:
        params = RM.fit_hmm(X)
        alpha = RM.filtered_probs(params, X)
        lab = pd.Series([params["labels"][int(a)] for a in alpha.argmax(axis=1)], index=X.index)
        ds2 = ds.copy()
        ds2["regime_label"] = ds2["tarih"].map(lab)
        for L, sub in ds2.dropna(subset=["regime_label"]).groupby("regime_label"):
            ic_r = daily_rank_ic(sub, zc, "fwd_ret")
            if len(ic_r) < 60:
                continue
            prior["regime_ic"][L] = {"ic_mean": {k: round(float(ic_r[f"z_{k}"].mean()), 5) for k in C.FACTORS},
                                     "n_dates": int(len(ic_r)), "n_eff_dates": round(len(ic_r) / C.LABEL_HORIZON, 1)}
        prior["hmm_state_stats"] = {params["labels"][k]: {"mean_daily_ret": params["state_mean_daily_ret"][k],
                                                          "ann_vol": params["state_ann_vol"][k]} for k in range(params["K"])}
    if oos_scores is not None and not oos_scores.empty:
        o = oos_scores.merge(ds[["tarih", "ticker", "barrier_gross"]], on=["tarih", "ticker"], how="inner")
        d = add_excess(o)
        if not d.empty:
            prior["calibration"] = {"buckets": bucket_table(d), "cutoffs": cutoff_stats(d), "source": "walk_forward_oos"}
    return prior


def run(start: str, end: Optional[str], save: bool = True, data: Optional[Dict] = None,
        regime_df: Optional[pd.DataFrame] = None) -> Dict:
    state = load_state()
    data = data if data is not None else download_ohlcv(UNIVERSE, start, end)
    snaps, dates, panel = build_inputs(data, state.get("sector_map", {}))
    if len(dates) < MIN_TRAIN_SESSIONS + 60:
        raise RuntimeError(f"Walk-forward için yetersiz seans: {len(dates)}")
    labels = compute_labels(panel)
    ds = factor_dataset(snaps, dates, labels)
    X = None
    try:
        rdf = regime_df if regime_df is not None else RM.download_regime_series(start=start, end=end)
        X = RM.make_features(rdf)
    except Exception as exc:
        print(f"⚠️ Rejim serisi alınamadı, backtest rejimsiz sürüyor: {exc}")
    wf = run_walk_forward(snaps, dates, panel, ds, X)
    prior = build_research_prior(ds, X, wf["oos_scores"])
    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "engine_version": C.ENGINE_VERSION,
        "period": f"{str(pd.Timestamp(dates[0]).date())}..{str(pd.Timestamp(dates[-1]).date())}",
        "universe_requested": len(UNIVERSE), "universe_downloaded": len(data),
        "data_source": "Yahoo Finance adjusted OHLCV (free) + XU100/USDTRY for regime",
        "synthetic_data_used": False,
        "cost_round_trip_pct": C.COST_ROUND_TRIP_PCT,
        "trade_rule": {"horizon": C.LABEL_HORIZON, "max_hold": C.MAX_HOLD, "stop_atr_mult": C.STOP_ATR_MULT,
                       "target_rr": C.TARGET_RR, "max_open": C.MAX_OPEN_POSITIONS},
        "oos_trades": trade_metrics(wf["trades"]),
        "oos_portfolio": portfolio_metrics(wf["portfolio_daily"]),
        "oos_composite_ic": wf["oos_ic"],
        "stability": stability(wf["fold_trade_metrics"]),
        "per_year": wf["per_year"],
        "folds": wf["folds"],
        "factor_ic_full_sample": {"ic_mean": prior["ic_mean"], "t_nw": prior["ic_t_nw"]},
        "limitations": ["survivorship: universe = today's liquid names",
                        "no intraday data: same-bar stop/target resolved pessimistically",
                        "sector neutralisation only for tickers in live sector_map"],
    }
    print(json.dumps({k: report[k] for k in ("period", "oos_trades", "oos_portfolio", "oos_composite_ic", "stability")},
                     ensure_ascii=False, indent=2, default=str))
    if save:
        atomic_json_write(C.RESEARCH_PRIOR_FILE, prior)
        atomic_json_write(C.BACKTEST_REPORT_FILE, report)
    return {"report": report, "prior": prior}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-date", default="2019-01-01")
    ap.add_argument("--end-date", default=None)
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    run(a.start_date, a.end_date, save=not a.no_save)


if __name__ == "__main__":
    main()
