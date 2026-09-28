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
    return {"days": days, "hist": hist, "idx": idx, "fx": fx, "cpi": cpi, "q": q, "tick": tick,
            "sectors": sectors, "rng": rng}


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
    # catastrophe stop
    pf = new_portfolio("2025-01-02")
    pf["pending"] = [{"ticker": "X", "action": "BUY", "reason": "T", "target_w": 0.1}]
    b = pd.DataFrame({"open": [10.0], "close": [10.0], "chg_pct": [0.0]}, index=["X"])
    apply_day(pf, b, "2025-01-02")
    for i, px in enumerate([9.0, 8.0, 7.2, 6.8]):
        prev = [10.0, 9.0, 8.0, 7.2][i]
        apply_day(pf, pd.DataFrame({"open": [px], "close": [px], "chg_pct": [(px / prev - 1) * 100]}, index=["X"]),
                  f"2025-01-0{3 + i}")
    res["catastrophe_queued"] = any(o["reason"] == "CATASTROPHE_STOP" for o in pf["pending"])
    ev, lots = apply_day(pf, pd.DataFrame({"open": [6.7], "close": [6.9], "chg_pct": [1.0]}, index=["X"]), "2025-01-09")
    res["catastrophe_executed"] = bool(lots) and lots[0]["reason"] == "CATASTROPHE_STOP"
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


def run_self_test() -> bool:
    import main as M
    import autonomy_guard as AG
    assert os.path.abspath(C.DATA_DIR) != os.path.abspath("data"), "self-test must not use ./data"
    W = synthetic_world()
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
        r = M.run(today=d, fetch=fetch, hist_fn=hist_fn, cpi_fn=cpi_fn, regime_fn=regime_fn)
        statuses.append(r["status"])
        reviews += int(bool(r.get("review")))
    # stale detection: re-run same data on next calendar session
    r2 = M.run(today=cal.add_sessions(live_days[-1], 1), fetch=fetch, hist_fn=hist_fn, cpi_fn=cpi_fn, regime_fn=regime_fn)

    from state_manager import load_monthly_snapshots, load_nav, load_state, load_trade_log
    st, snaps, nav, tl = load_state(), load_monthly_snapshots(), load_nav(), load_trade_log()
    u = unit_checks()

    # small walk-forward backtest on the same synthetic history (no fundamentals history)
    import backtest_optimizer as B
    sub = {t: g[g.index <= pd.Timestamp("2026-05-29")] for t, g in list(W["hist"].items())[:70]}
    bt = B.run("2019-01-01", None, save=False, data=sub, regime_df=reg_df, cpi=W["cpi"], pit=pd.DataFrame())
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
        **{f"unit_{k}": bool(v) for k, v in u.items()},
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
