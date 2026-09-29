"""End-to-end self-test of V3 on SYNTHETIC data in a temporary folder.

`python main.py --self-test`. Network calls are replaced by in-memory
generators; ./data is never touched. The synthetic market has a persistent
'quality' edge (visible in fundamentals and prices), inflation, a bonus issue,
and runs ~20 months of daily sessions so monthly reviews, learning,
calibration, orders, NAV and the trade log are all exercised. Also runs unit
checks of the catastrophe stop, CPI splicing, fundamentals TTM logic and a
small walk-forward backtest.
"""
from __future__ import annotations

import os
import sys
import tempfile

if "BOQ_DATA_DIR" not in os.environ:
    os.environ["BOQ_DATA_DIR"] = tempfile.mkdtemp(prefix="boq3_selftest_")
os.environ.pop("EVDS_API_KEY", None)      # the self-test must never touch real data sources

import numpy as np
import pandas as pd

import calendar_tr as cal
import config as C


def synthetic_world(n=80, start="2019-01-02", end="2026-06-30", seed=5):
    rng = np.random.default_rng(seed)
    days = pd.DatetimeIndex(cal.sessions_between(start, end))
    T = len(days)
    q = rng.normal(0, 1, n)                                   # latent quality
    mkt = rng.normal(0.0011, 0.014, T)                        # ~30%/yr nominal market
    close = np.zeros((T, n))
    close[0] = rng.uniform(5, 150, n)
    mom = np.zeros(n)
    for t in range(1, T):
        mom = 0.995 * mom + rng.normal(0, 0.00008, n)
        r = mkt[t] * rng.uniform(0.7, 1.3, n) + 0.0005 * q + mom + rng.normal(0, 0.02, n)
        close[t] = close[t - 1] * np.exp(np.clip(r, -0.095, 0.095))
    opn = np.vstack([close[0], close[:-1] * np.exp(rng.normal(0, 0.005, (T - 1, n)))])
    vol = rng.lognormal(14, 0.6, (T, n))
    tick = [f"SYN{i:03d}" for i in range(n)]
    hist = {t: pd.DataFrame({"open": opn[:, j], "high": np.maximum(opn[:, j], close[:, j]) * 1.01,
                             "low": np.minimum(opn[:, j], close[:, j]) * 0.99, "close": close[:, j],
                             "volume": vol[:, j]}, index=days) for j, t in enumerate(tick)}
    idx = pd.Series(1000 * np.exp(np.cumsum(mkt)), index=days)
    fx = pd.Series(5 * np.exp(np.cumsum(rng.normal(0.0008, 0.005, T))), index=days)
    months = pd.date_range("2016-01-01", "2026-12-01", freq="MS")
    cpi = pd.Series(100 * np.exp(np.cumsum(np.full(len(months), 0.02) + rng.normal(0, 0.004, len(months)))), index=months)
    sectors = [f"S{j % 7}" for j in range(n)]
    gold_usd = pd.Series(1300 * np.exp(np.cumsum(rng.normal(0.0003, 0.009, T))), index=days)
    crate = pd.Series(np.full(len(months), 25.0), index=months)
    return {"days": days, "hist": hist, "idx": idx, "fx": fx, "cpi": cpi, "q": q, "tick": tick,
            "sectors": sectors, "rng": rng, "gold_usd": gold_usd, "crate": crate}


def bench_of(W):
    import benchmarks as BMk
    return BMk.assemble(W["fx"], W["gold_usd"], W["idx"])


def _run(W, **kw):
    """main.run with every external source replaced by synthetic data."""
    import main as M
    kw.setdefault("bench_fn", lambda: bench_of(W))
    kw.setdefault("cash_fn", lambda: (W["crate"], {"source": "synthetic"}))
    return M.run(**kw)


def snapshot_for(W, d, split=None):
    """TradingView-like snapshot. `split` = (ticker, date, ratio) applied to RAW prices after date."""
    rows = []
    for j, t in enumerate(W["tick"]):
        g = W["hist"][t]
        if d not in g.index:
            continue
        i = g.index.get_loc(d)
        o, c = g["open"].iloc[i], g["close"].iloc[i]
        chg = (c / g["close"].iloc[i - 1] - 1) * 100 if i > 0 else 0.0
        k = 1.0
        if split and t == split[0] and d >= split[1]:
            k = split[2]
        q = W["q"][j]
        rows.append({"ticker": t, "open": o * k, "high": g["high"].iloc[i] * k, "low": g["low"].iloc[i] * k,
                     "close": c * k, "volume": g["volume"].iloc[i], "change_pct": chg,
                     "value_traded": c * g["volume"].iloc[i], "market_cap": c * 1e8, "sector": W["sectors"][j],
                     "roe": 20 + 8 * q + np.random.normal(0, 3), "pe": max(3.0, 9 - 2 * q + np.random.normal(0, 1)),
                     "pb": max(0.3, 1.5 - 0.3 * q), "ps": 1.0, "net_income": np.nan, "revenue": np.nan,
                     "rev_growth": 35 + 10 * q, "op_margin": 12 + 4 * q, "debt_to_equity": max(0.0, 1 - 0.3 * q),
                     "div_yield": max(0.0, 2 + q)})
    return pd.DataFrame(rows)


def unit_checks() -> dict:
    import inflation as INF
    import fundamentals_hist as FH
    from portfolio import apply_day, new_portfolio
    res = {}
    # drawdown: -32% -> FLAG only (no sell); -51% from entry -> hard stop sell at next open
    pf = new_portfolio("2025-01-02")
    pf["pending"] = [{"ticker": "X", "action": "BUY", "reason": "T", "target_w": 0.1}]
    b = pd.DataFrame({"open": [10.0], "close": [10.0], "chg_pct": [0.0]}, index=["X"])
    apply_day(pf, b, "2025-01-02")
    path = [10.0, 9.0, 8.0, 7.2, 6.8]
    for i in range(1, len(path)):
        px, prev = path[i], path[i - 1]
        apply_day(pf, pd.DataFrame({"open": [px], "close": [px], "chg_pct": [(px / prev - 1) * 100]}, index=["X"]),
                  f"2025-01-0{2 + i}")
    res["drawdown_flag_not_sold"] = bool(pf["positions"]["X"].get("dd_flag")) and not pf["pending"]
    apply_day(pf, pd.DataFrame({"open": [4.9], "close": [4.9], "chg_pct": [(4.9 / 6.8 - 1) * 100]}, index=["X"]), "2025-01-08")
    res["hard_stop_queued"] = any(o["reason"] == "CATASTROPHE_STOP" for o in pf["pending"])
    ev, lots = apply_day(pf, pd.DataFrame({"open": [4.8], "close": [4.9], "chg_pct": [0.0]}, index=["X"]), "2025-01-09")
    res["hard_stop_executed"] = bool(lots) and lots[0]["reason"] == "CATASTROPHE_STOP"
    # flagged position with intact thesis is KEPT at the review; with weak thesis it is SOLD
    from meta_engine import plan_rebalance
    pf3 = new_portfolio("2025-01-02")
    pf3["cash"] = 0.8
    pf3["positions"] = {"A": {"value": 0.1, "cost_basis": 0.15, "entry_date": "2025-01-02", "level": 0.6, "peak": 1.0, "dd_flag": True},
                        "B": {"value": 0.1, "cost_basis": 0.15, "entry_date": "2025-01-02", "level": 0.6, "peak": 1.0, "dd_flag": True}}
    fr = pd.DataFrame({"ticker": ["A", "B", "C"], "composite_pct": [95.0, 70.0, 50.0], "composite": [2, 1, 0],
                       "med_value_traded": [1e9] * 3, "fund_break": [False] * 3, "exp_real_12m": [10.0] * 3,
                       "vol_ann_pct": [40.0] * 3, "sector": ["S1", "S2", "S3"]})
    orders, summ = plan_rebalance(pf3, fr, {"calibration": {"pct_cutoff": 85.0, "status": "CALIBRATED"}}, 1.0)
    res["dd_thesis_intact_kept"] = "A" in summ["holds"]
    res["dd_thesis_failed_sold"] = any(o["ticker"] == "B" and o["reason"] == "DRAWDOWN_CONFIRMED" for o in orders)
    # cash yield: funding rate 40% -> (40-2)*0.85 = 32.3% net
    import inflation as I2
    res["cash_yield"] = abs(I2.cash_yield_at(pd.Series([40.0], index=[pd.Timestamp("2025-01-01")]), "2025-03-10") - 32.3) < 1e-9
    # bonus issue neutrality: raw price halves, adjusted change +1% -> value +1%
    pf2 = new_portfolio("2025-01-02")
    pf2["pending"] = [{"ticker": "Y", "action": "BUY", "reason": "T", "target_w": 0.5}]
    apply_day(pf2, pd.DataFrame({"open": [20.0], "close": [20.0], "chg_pct": [0.0]}, index=["Y"]), "2025-01-02")
    v0 = pf2["positions"]["Y"]["value"]
    apply_day(pf2, pd.DataFrame({"open": [10.0], "close": [10.1], "chg_pct": [1.0]}, index=["Y"]), "2025-01-03")
    res["bonus_issue_neutral"] = abs(pf2["positions"]["Y"]["value"] / v0 - 1.01) < 1e-9
    # CPI splice
    a = pd.Series(np.linspace(100, 200, 40), index=pd.date_range("2020-01-01", periods=40, freq="MS"))
    bb = pd.Series(np.linspace(50, 120, 50), index=pd.date_range("2021-01-01", periods=50, freq="MS"))
    res["cpi_splice"] = len(INF.splice(a, bb)) == 62
    # fundamentals TTM from YTD cumulative statements
    raw = pd.DataFrame([
        {"ticker": "Z", "year": 2023, "period": 12, "net_income": 100, "revenue": 1000, "op_profit": 150, "equity": 500, "fin_debt": 100, "paid_in": 10},
        {"ticker": "Z", "year": 2023, "period": 3, "net_income": 20, "revenue": 200, "op_profit": 30, "equity": 450, "fin_debt": 90, "paid_in": 10},
        {"ticker": "Z", "year": 2024, "period": 3, "net_income": 40, "revenue": 300, "op_profit": 50, "equity": 560, "fin_debt": 80, "paid_in": 10},
    ])
    pit = FH.build_point_in_time(raw)
    r = pit[(pit["year"] == 2024) & (pit["period"] == 3)].iloc[0]
    res["ttm_logic"] = abs(r["net_income_ttm"] - 120) < 1e-9 and abs(r["revenue_ttm"] - 1100) < 1e-9
    res["pit_lag"] = FH.point_in_time(pit, "2024-05-01", ["Z"]).iloc[0]["year"] == 2023 if not pit.empty else False
    return res


def inflation_gate_check(W) -> dict:
    """Day 1: no CPI and no FX proxy -> review must block buys. Day 2 (same month): CPI back ->
    the ungated review must be redone and buys must appear."""
    import main as M
    import state_manager as SM
    days = [d for d in W["days"] if d >= pd.Timestamp("2025-06-02")][:2]
    reg_df = pd.DataFrame({"idx": W["idx"], "fx": W["fx"]})
    cur = {"d": days[0]}
    fetch = lambda st: (snapshot_for(W, cur["d"]), {"source": "synthetic"})
    hist_fn = lambda tickers, start: {t: g[(g.index >= pd.Timestamp(start)) & (g.index <= cur["d"])]
                                      for t, g in W["hist"].items() if t in tickers}

    def no_regime():
        raise RuntimeError("offline")
    r1 = _run(W, today=days[0], fetch=fetch, hist_fn=hist_fn, regime_fn=no_regime,
               cpi_fn=lambda: (pd.Series(dtype=float), {"status": "UNAVAILABLE"}))
    st1 = SM.load_state()
    blocked = r1["review"] is not None and not st1["portfolio"]["pending"] and \
        st1["last_rebalance"].get("expected_inflation_12m") is None
    cur["d"] = days[1]
    last = pd.Timestamp(days[1]) - pd.DateOffset(months=1)
    r2 = _run(W, today=days[1], fetch=fetch, hist_fn=hist_fn, regime_fn=lambda: reg_df[reg_df.index <= cur["d"]],
               cpi_fn=lambda: (W["cpi"][W["cpi"].index <= pd.Timestamp(last.year, last.month, 1)], {"status": "OK", "source": "synthetic"}))
    st2 = SM.load_state()
    redone = r2["review"] is not None and r2["review"].get("status") == "OK" and \
        st2["last_rebalance"].get("expected_inflation_12m") is not None
    # proxy path: CPI unavailable but FX series present
    r3 = _run(W, today=pd.Timestamp(cal.add_sessions(days[1], 1)), fetch=lambda st: (snapshot_for(W, cal.add_sessions(days[1], 1)), {}),
               hist_fn=hist_fn, regime_fn=lambda: reg_df, cpi_fn=lambda: (pd.Series(dtype=float), {"status": "UNAVAILABLE"}))
    st3 = SM.load_state()
    proxy = st3["inflation"].get("status") == "PROXY" and st3["inflation"].get("expected_12m_pct") is not None
    for f in (C.STATE_FILE, C.NAV_FILE, C.MONTHLY_SNAPSHOT_FILE, C.TRADE_LOG_FILE):
        if os.path.exists(f):
            os.remove(f)
    # intraday REFRESH: ungated review (no CPI) is redone during the session without trading
    cur["d"] = days[0]
    _run(W, today=days[0], fetch=fetch, hist_fn=hist_fn, regime_fn=no_regime,
          cpi_fn=lambda: (pd.Series(dtype=float), {"status": "UNAVAILABLE"}))
    n_nav = len(SM.load_nav())
    cur["d"] = days[1]
    rr = _run(W, today=days[1], refresh=True, fetch=fetch, hist_fn=hist_fn,
               regime_fn=lambda: reg_df[reg_df.index <= cur["d"]],
               cpi_fn=lambda: (W["cpi"][W["cpi"].index <= pd.Timestamp(last.year, last.month, 1)], {"status": "OK", "source": "synthetic"}))
    str_ = SM.load_state()
    refresh_ok = rr["review"] is not None and len(SM.load_nav()) == n_nav and \
        str_["last_rebalance"].get("expected_inflation_12m") is not None and str_["last_run"].get("mode") == "REFRESH" \
        and str_["last_rebalance"].get("date") == str(days[0].date())
    for f in (C.STATE_FILE, C.NAV_FILE, C.MONTHLY_SNAPSHOT_FILE, C.TRADE_LOG_FILE):
        if os.path.exists(f):
            os.remove(f)
    return {"no_cpi_blocks_buys": bool(blocked), "ungated_review_redone": bool(redone), "fx_proxy_used": bool(proxy),
            "intraday_refresh": bool(refresh_ok)}


def _gold_ok() -> bool:
    """dual overlay rotating into gold: plan must sell stocks and buy the gold sleeve."""
    from meta_engine import plan_rebalance
    from portfolio import new_portfolio
    pf = new_portfolio("2025-01-02")
    pf["cash"] = 0.9
    pf["positions"] = {"A": {"value": 0.1, "cost_basis": 0.1, "entry_date": "2025-01-02", "level": 1.0, "peak": 1.0}}
    fr = pd.DataFrame({"ticker": ["A", "B"], "composite_pct": [95.0, 96.0], "composite": [1, 2], "med_value_traded": [1e9] * 2,
                       "fund_break": [False] * 2, "exp_real_12m": [10.0] * 2, "vol_ann_pct": [40.0] * 2, "sector": ["S1", "S2"]})
    orders, summ = plan_rebalance(pf, fr, {"calibration": {"status": "CALIBRATED"}}, 1.0,
                                  params={"n_positions": 5, "buy_pct": 90, "gate": "rank", "overlay": "dual"},
                                  equity_frac=0.0, gold_w=1.0)
    return any(o["ticker"] == "A" and o["action"] == "SELL" for o in orders) and \
        any(o["ticker"] == "ALTIN" and o["action"] == "BUY" for o in orders) and not summ["buys"]


def _tg_ok(st) -> bool:
    import telegram_report as TG
    rev = {"target_weights": {"AAA": 0.1}, "sells": ["BBB"], "sell_reasons": {"BBB": "RANK_EXIT"}, "holds": ["CCC"]}
    msg = TG.monthly_report(pd.Timestamp("2026-10-01"), st, rev, [{"ticker": "AAA", "exp_nominal_12m": 55.2, "p_beat_all": 0.61}])
    ev = TG.events_report(pd.Timestamp("2026-10-02"), [{"ticker": "AAA", "type": "BUY", "w": 0.1},
                                                       {"ticker": "BBB", "type": "SELL_RANK_EXIT", "ret": 12.3}], st, 0.8)
    print("\n--- TELEGRAM (aylık) ---\n" + msg + "\n--- TELEGRAM (olay) ---\n" + ev + "\n---")
    return "Çıta" in msg or "çıta" in msg


def run_self_test() -> bool:
    import main as M
    import autonomy_guard as AG
    assert os.path.abspath(C.DATA_DIR) != os.path.abspath("data"), "self-test must not use ./data"
    W = synthetic_world()
    gate = inflation_gate_check(W)
    split = ("SYN003", pd.Timestamp("2025-03-03"), 0.5)
    live_days = [d for d in W["days"] if pd.Timestamp("2024-11-01") <= d <= pd.Timestamp("2026-05-29")]
    reg_df = pd.DataFrame({"idx": W["idx"], "fx": W["fx"]})
    cur = {"d": None}

    def fetch(state):
        return snapshot_for(W, cur["d"], split), {"source": "synthetic"}

    def hist_fn(tickers, start):
        return {t: g[(g.index >= pd.Timestamp(start)) & (g.index <= cur["d"])] for t, g in W["hist"].items() if t in tickers}

    def cpi_fn():
        last = pd.Timestamp(cur["d"]) - pd.DateOffset(months=1)
        s = W["cpi"][W["cpi"].index <= pd.Timestamp(last.year, last.month, 1)]
        return s, {"source": "synthetic", "status": "OK"}

    def regime_fn():
        return reg_df[reg_df.index <= cur["d"]]

    statuses, reviews = [], 0
    for d in live_days:
        cur["d"] = d
        r = _run(W, today=d, fetch=fetch, hist_fn=hist_fn, cpi_fn=cpi_fn, regime_fn=regime_fn)
        statuses.append(r["status"])
        reviews += int(bool(r.get("review")))
    # stale detection: re-run same data on next calendar session
    r2 = _run(W, today=cal.add_sessions(live_days[-1], 1), fetch=fetch, hist_fn=hist_fn, cpi_fn=cpi_fn, regime_fn=regime_fn)

    from state_manager import load_monthly_snapshots, load_nav, load_state, load_trade_log
    st, snaps, nav, tl = load_state(), load_monthly_snapshots(), load_nav(), load_trade_log()
    u = unit_checks()

    # small walk-forward backtest on the same synthetic history (no fundamentals history)
    import backtest_optimizer as B
    sub = {t: g[g.index <= pd.Timestamp("2026-05-29")] for t, g in list(W["hist"].items())[:70]}
    import strategy_lab as LAB
    small = [v for v in LAB.variant_grid() if v["name"] in ("n5_b90_eq_rank_dual", "n8_b85_iv_hurdle_trend", "n12_b85_eq_rank_none")]
    bt = B.run("2019-01-01", None, save=True, data=sub, regime_df=reg_df, cpi=W["cpi"], pit=pd.DataFrame(),
               bm=bench_of(W), crate=W["crate"], variants=small)
    rep = bt["report"]

    checks = {
        "all_runs_ok": all(s == "OK" for s in statuses),
        "stale_detected": r2["status"] == "STALE",
        "monthly_reviews": reviews >= 18,
        "positions_held": len(st["portfolio"]["positions"]) > 0,
        "nav_recorded": len(nav) >= len(live_days) - 1,
        "labels_resolved": "fwd_3m" in snaps and snaps["fwd_3m"].notna().sum() > 0,
        "learning_ran": not str(st["model"].get("status", "")).startswith("BOOTSTRAP"),
        "guard_self_test": AG.run_self_test()["passed"],
        "real_return_reported": st["performance"]["nav"].get("real_total_pct") is not None,
        "backtest_ran": rep["portfolio"].get("days", 0) > 200,
        "hurdles_computed": (st.get("hurdles") or {}).get("hurdle") is not None and (st.get("hurdles") or {}).get("gold") is not None,
        "beat_all_labels": "beat_all" in snaps and snaps["beat_all"].notna().sum() > 0,
        "multi_bench_report": "rolling12m_beat" in rep["portfolio"] and "beat_all" in next(iter(rep["per_year"].values())),
        "strategy_lab_ran": rep.get("strategy_lab", {}).get("variants_tested", 0) >= 4 and os.path.exists(C.STRATEGY_CONFIG_FILE),
        "gold_sleeve_unit": _gold_ok(),
        "telegram_monthly_ok": _tg_ok(st),
        **{f"unit_{k}": bool(v) for k, v in u.items()},
        **{f"gate_{k}": v for k, v in gate.items()},
    }
    print("SELF-TEST checks:", checks)
    print("live performance:", st["performance"]["nav"])
    print("lots:", st["performance"]["lots"])
    print("model:", st["model"].get("status"), "| calibration:", st["calibration"].get("status"))
    print("backtest:", rep["portfolio"], rep["oos_composite_ic_12m"])
    ok = all(checks.values())
    print("✅ SELF-TEST PASSED" if ok else "❌ SELF-TEST FAILED", "| temp:", C.DATA_DIR)
    return ok


if __name__ == "__main__":
    sys.exit(0 if run_self_test() else 1)
