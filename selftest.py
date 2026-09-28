"""End-to-end self-test on SYNTHETIC data in a temporary folder.

`python main.py --self-test` (or `python selftest.py`). Nothing is written to
./data; network calls are replaced by in-memory generators. The synthetic
market has a known momentum edge, a 2:1 bonus issue, a missed run and a
holiday-like stale run, so every critical path is exercised.
"""
from __future__ import annotations

import os
import sys
import tempfile

if "BOQ_DATA_DIR" not in os.environ:
    os.environ["BOQ_DATA_DIR"] = tempfile.mkdtemp(prefix="boq_selftest_")

import numpy as np
import pandas as pd

import calendar_tr as cal
import config as C


def synthetic_market(n_tickers=90, n_sessions=150, seed=7):
    rng = np.random.default_rng(seed)
    days = cal.sessions_between("2025-01-02", "2026-12-31")[: n_sessions + 70]
    T = len(days)
    tickers = [f"SYN{i:03d}" for i in range(n_tickers)]
    drift = rng.normal(0, 0.0015, n_tickers)
    mkt = rng.normal(0.0004, 0.012, T)
    close = np.zeros((T, n_tickers))
    close[0] = rng.uniform(5, 200, n_tickers)
    for t in range(1, T):
        drift = 0.98 * drift + rng.normal(0, 0.0003, n_tickers)   # persistent -> momentum edge
        r = mkt[t] + drift + rng.normal(0, 0.018, n_tickers)
        close[t] = close[t - 1] * np.exp(np.clip(r, -0.095, 0.095))
    opn = close * np.exp(rng.normal(0, 0.004, close.shape))
    opn[1:] = close[:-1] * np.exp(rng.normal(0, 0.006, (T - 1, n_tickers)))
    high = np.maximum(opn, close) * (1 + rng.uniform(0, 0.02, close.shape))
    low = np.minimum(opn, close) * (1 - rng.uniform(0, 0.02, close.shape))
    vol = rng.lognormal(13, 0.5, close.shape)
    return days, tickers, opn, high, low, close, vol, mkt


def snapshots_from(days, tickers, O, H, L, Cl, V, split=None):
    """Raw TradingView-like snapshots; optional split (ticker_idx, day_idx, ratio) on RAW prices."""
    O, H, L, Cl = O.copy(), H.copy(), L.copy(), Cl.copy()
    true_close = Cl.copy()
    if split:
        j, d, ratio = split
        for M in (O, H, L, Cl):
            M[d:, j] *= ratio
    frames = {}
    df_true = pd.DataFrame(true_close, index=days, columns=tickers)
    for t in range(70, len(days)):
        lo, hi = max(0, t - 20), t + 1
        rows = pd.DataFrame({
            "ticker": tickers, "open": O[t], "high": H[t], "low": L[t], "close": Cl[t], "volume": V[t],
            "change_pct": (df_true.iloc[t] / df_true.iloc[t - 1] - 1).to_numpy() * 100,
            "value_traded": Cl[t] * V[t] * 5,
            "rvol": V[t] / V[t - 10:t].mean(axis=0),
            "perf_w": (df_true.iloc[t] / df_true.iloc[t - 5] - 1).to_numpy() * 100,
            "perf_1m": (df_true.iloc[t] / df_true.iloc[t - 21] - 1).to_numpy() * 100,
            "perf_3m": (df_true.iloc[t] / df_true.iloc[t - 63] - 1).to_numpy() * 100,
            "high_1m": H[lo:hi].max(axis=0), "low_1m": L[lo:hi].min(axis=0),
            "atr": (H[t - 14:t + 1] - L[t - 14:t + 1]).mean(axis=0),
            "market_cap": Cl[t] * 1e8, "sector": [f"S{i % 6}" for i in range(len(tickers))],
            "takas_conc": np.nan, "takas_conf": 0.0,
        })
        frames[days[t]] = rows
    return frames


def run_self_test() -> bool:
    import main as M
    import regime_model as RM
    import autonomy_guard as AG

    assert os.path.abspath(C.DATA_DIR) != os.path.abspath("data"), "self-test must not use ./data"
    days, tickers, O, H, L, Cl, V, mkt = synthetic_market()
    split_day = 120
    frames = snapshots_from(days, tickers, O, H, L, Cl, V, split=(5, split_day, 0.5))
    idx = pd.Series(1000 * np.exp(np.cumsum(mkt)), index=pd.DatetimeIndex(days))
    fx = pd.Series(30 * np.exp(np.cumsum(np.random.default_rng(3).normal(0.0005, 0.005, len(days)))), index=idx.index)
    pre = pd.DataFrame({"idx": np.r_[1000 * np.exp(np.cumsum(np.random.default_rng(4).normal(0, 0.012, 400)))],
                        "fx": np.r_[20 * np.exp(np.cumsum(np.random.default_rng(5).normal(0.0005, 0.005, 400)))]},
                       index=pd.bdate_range(end=days[0] - pd.Timedelta(days=1), periods=400))
    reg_full = pd.concat([pre, pd.DataFrame({"idx": idx * pre["idx"].iloc[-1] / 1000,
                                             "fx": fx * pre["fx"].iloc[-1] / 30})])
    current = {"d": None}
    RM.download_regime_series = lambda *a, **k: reg_full[reg_full.index <= current["d"]]

    run_days = [d for d in days[70:]]
    missed = run_days[100]                         # simulate a skipped workflow run
    results = []
    guard_log = []
    for k, d in enumerate(run_days):
        if d == missed:
            continue
        current["d"] = d
        res = M.run(fetcher=lambda st, _d=d: (frames[_d].copy(), {"source": "synthetic"}), today=d)
        results.append(res["status"])
        if res["status"] == "OK":
            g = res["state"]["autonomy_guard"]
            guard_log.append((str(d.date()), g["mode"], g["reason"], g["drift_score"], g["performance_drift"],
                              g["ops_score"], g["regime_stress"]))
        if k == 60:   # stale re-run on the next calendar day with identical data -> must be skipped
            nd = cal.add_sessions(d, 1)
            r2 = M.run(fetcher=lambda st, _d=d: (frames[_d].copy(), {"source": "synthetic"}), today=nd, force=False)
            assert r2["status"] == "STALE", r2["status"]

    from state_manager import load_ledger, load_snapshots, load_state
    state, ledger, snaps = load_state(), load_ledger(), load_snapshots()
    checks = {
        "all_runs_ok": all(s == "OK" for s in results),
        "guard_self_test": AG.run_self_test()["passed"],
        "ledger_has_closed": int((ledger["status"] == "CLOSED").sum()) > 0,
        "missed_entry_handled": bool((ledger.loc[ledger["signal_date"] == run_days[99], "status"] == "MISSED").all()
                                     and not (ledger["entry_date"] == missed).any()),
        "learning_progressed": not str(state["model"].get("status", "")).startswith("BOOTSTRAP"),
        "live_ic_measured": state["model"].get("composite_ic_live", {}).get("n_dates", 0) > 0,
        "regime_ok": state["regime"].get("source") == "hmm_yfinance",
        "snapshot_cap": snaps["tarih"].nunique() <= C.MAX_SNAPSHOT_SESSIONS,
        "ca_detected": any(e.get("ticker") == tickers[5] for e in
                           __import__("data_integrity").build_price_panel(snaps)["ca_events"]),
        "no_synthetic_in_repo_data": not os.path.exists(os.path.join("data", "snapshots_eod.csv")) or
        not pd.read_csv(os.path.join("data", "snapshots_eod.csv"), nrows=50)["ticker"].astype(str).str.startswith("SYN").any(),
    }
    modes = pd.Series([g[1] for g in guard_log]).value_counts().to_dict()
    print("guard modes:", modes)
    for g in guard_log[::10]:
        print("  guard", g)
    print("SELF-TEST checks:", checks)
    print("model:", state["model"].get("status"), "| live IC:", state["model"].get("composite_ic_live", {}).get("ic_mean"))
    print("performance:", state.get("performance"))
    ok = all(checks.values())
    print("✅ SELF-TEST PASSED" if ok else "❌ SELF-TEST FAILED", "| temp dir:", C.DATA_DIR)
    return ok


if __name__ == "__main__":
    sys.exit(0 if run_self_test() else 1)
