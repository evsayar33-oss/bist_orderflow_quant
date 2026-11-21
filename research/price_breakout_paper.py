"""Independent 90-day prospective shadow ledger: baseline vs locked price breakout.

No orders, no writes to production state, no historical backfill. Signals come
only from the already-persisted monthly snapshot; executions are next-session
OPEN with explicit cost assumptions. Missing prices fail closed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
import numpy as np

from state_manager import load_monthly_snapshots
from market_data import download_history

COSTS = {"1pct": 0.005, "2pct": 0.01}  # each side, total round-trip
STRATEGIES = ("baseline", "price_breakout")
INITIAL_NAV = 100_000.0
DAYS = 90
MIN_COVERAGE = 0.95


def score(frame, name):
    """Exact EXTRA_BLEND rank logic for locked high_52w / trend variant."""
    f = frame.copy()
    base = pd.to_numeric(f["composite"], errors="coerce")
    z = (base - base.mean()) / (base.std() or 1.0)
    if name == "baseline":
        # Production baseline already has its own EXTRA_BLEND. Reconstruct it
        # from persisted factors; do not silently compare against raw composite.
        keys = {"sector_mom": 0.25, "ep_x_quality": 1.0, "quality_mom": 0.25}
        mom = pd.to_numeric(f["f_mom_12_1"], errors="coerce")
        roe = pd.to_numeric(f["f_roe"], errors="coerce")
        ey = pd.to_numeric(f["f_earnings_yield"], errors="coerce")
        xs = {
            "sector_mom": mom.groupby(f["sector"].fillna("NA")).transform("mean"),
            "ep_x_quality": ey.rank(pct=True) * roe.rank(pct=True),
            "quality_mom": roe.rank(pct=True) * mom.rank(pct=True),
        }
    else:
        keys = {"high_52w": 0.5, "trend": 0.25}
        xs = {
            "high_52w": pd.to_numeric(f["f_high_52w"], errors="coerce"),
            "trend": pd.to_numeric(f["f_trend_consistency"], errors="coerce"),
        }
    for k, w in keys.items():
        x = xs[k]
        if x.notna().mean() < 0.3:
            continue
        r = x.rank(pct=True)
        z = z + w * ((r - r.mean()) / (r.std() or 1.0)).fillna(0)
    return z


def picks(frame, name, n=5):
    required = {"ticker", "composite", "sector", "med_value_traded",
                "f_mom_12_1", "f_roe", "f_earnings_yield",
                "f_high_52w", "f_trend_consistency"}
    if not required.issubset(frame.columns):
        raise ValueError("MISSING_MONTHLY_SNAPSHOT_FIELDS: " + ",".join(sorted(required-set(frame.columns))))
    import config as C
    f = frame.copy().drop_duplicates("ticker", keep="last")
    if len(f) < 20:
        raise ValueError("INSUFFICIENT_MONTHLY_CROSS_SECTION")
    liq = pd.to_numeric(f["med_value_traded"], errors="coerce")
    f = f[liq >= C.MIN_MEDIAN_VALUE_TRADED_TL].copy()
    if len(f) < n:
        raise ValueError("INSUFFICIENT_LIQUID_NAMES")
    f["_score"] = score(f, name)
    f = f.sort_values(["_score", "ticker"], ascending=[False, True])
    result, sectors = [], {}
    for _, r in f.iterrows():
        sec = str(r["sector"]) if pd.notna(r["sector"]) else "NA"
        if sec != "NA" and sectors.get(sec, 0) >= 2:
            continue
        result.append(str(r["ticker"]))
        sectors[sec] = sectors.get(sec, 0) + 1
        if len(result) == n:
            break
    if len(result) != n:
        raise ValueError("INSUFFICIENT_SECTOR_DIVERSITY")
    return result


def new_state(day):
    return {
        "schema": "BOQ_PRICE_BREAKOUT_PAPER_V1", "start": day, "last_day": None,
        "end_after": str((pd.Timestamp(day) + pd.Timedelta(days=DAYS)).date()),
        "no_real_orders": True, "initial_nav": INITIAL_NAV,
        "books": {
            f"{name}_{cost}": {"cash": INITIAL_NAV, "shares": {},
                               "pending": None, "last_signal_month": None,
                               "nav": INITIAL_NAV, "peak": INITIAL_NAV,
                               "max_drawdown_pct": 0.0}
            for name in STRATEGIES for cost in COSTS
        },
        "ledger": [], "trades": [],
    }


def step(state, day, bars, signal=None):
    """Advance exactly one completed market session; never infer missing fills."""
    if state["last_day"] is not None and day <= state["last_day"]:
        raise ValueError("NON_MONOTONIC_OR_DUPLICATE_SESSION")
    if day < state["start"] or day > state["end_after"]:
        raise ValueError("SESSION_OUTSIDE_PAPER_WINDOW")
    if not bars:
        raise ValueError("NO_MARKET_DATA")
    staged = json.loads(json.dumps(state))
    for book_key, book in staged["books"].items():
        name, cost = book_key.rsplit("_", 1)
        side_cost = COSTS[cost]
        pending = book["pending"]
        if pending:
            required = set(book["shares"]) | set(pending)
            missing = required - set(bars)
            if missing:
                raise ValueError("MISSING_EXECUTION_BAR:" + ",".join(sorted(missing)))
            # Liquidate at next open and buy selected names at same next open.
            # A full turnover each monthly signal is deliberately conservative.
            for ticker, qty in list(book["shares"].items()):
                op = float(bars[ticker]["open"])
                if not math.isfinite(op) or op <= 0:
                    raise ValueError("BAD_EXECUTION_OPEN:" + ticker)
                book["cash"] += qty * op * (1-side_cost)
                staged["trades"].append({"day": day, "book": book_key, "side": "SELL",
                                          "ticker": ticker, "qty": qty, "price": op})
            book["shares"] = {}
            n = len(pending)
            allocation = book["cash"] / n
            for ticker in pending:
                op = float(bars[ticker]["open"])
                if not math.isfinite(op) or op <= 0:
                    raise ValueError("BAD_EXECUTION_OPEN:" + ticker)
                qty = int(allocation / (op*(1+side_cost)))
                if qty <= 0:
                    raise ValueError("INSUFFICIENT_CASH_FOR_SHARE:" + ticker)
                book["cash"] -= qty * op * (1+side_cost)
                book["shares"][ticker] = qty
                staged["trades"].append({"day": day, "book": book_key, "side": "BUY",
                                          "ticker": ticker, "qty": qty, "price": op})
            book["pending"] = None
        missing = set(book["shares"]) - set(bars)
        if missing:
            raise ValueError("MISSING_MARK_TO_MARKET:" + ",".join(sorted(missing)))
        nav = book["cash"]
        for ticker, qty in book["shares"].items():
            close = float(bars[ticker]["close"])
            if not math.isfinite(close) or close <= 0:
                raise ValueError("BAD_MARK_CLOSE:" + ticker)
            nav += qty * close
        if not math.isfinite(nav) or nav <= 0:
            raise ValueError("INVALID_NAV")
        book["nav"] = round(nav, 4)
        book["peak"] = max(book["peak"], book["nav"])
        book["max_drawdown_pct"] = min(book["max_drawdown_pct"],
                                      round((book["nav"]/book["peak"]-1)*100, 4))
        staged["ledger"].append({"day": day, "book": book_key, "nav": book["nav"],
                                  "return_pct": round((book["nav"]/INITIAL_NAV-1)*100, 4),
                                  "max_drawdown_pct": book["max_drawdown_pct"]})
        if signal is not None and book["last_signal_month"] != day[:7]:
            book["pending"] = signal[name]
            book["last_signal_month"] = day[:7]
    staged["last_day"] = day
    return staged


def last_completed_weekday(now: pd.Timestamp) -> str:
    """Last potentially completed BIST trading day; never infer holiday prices."""
    local = now.tz_convert("Europe/Istanbul")
    date = local.normalize()
    if local.weekday() >= 5 or local.hour < 18 or (local.hour == 18 and local.minute < 15):
        date -= pd.Timedelta(days=1)
    while date.weekday() >= 5:
        date -= pd.Timedelta(days=1)
    return date.strftime("%Y-%m-%d")


def run(output):
    path = Path(output)
    state = json.loads(path.read_text()) if path.exists() else None
    now = pd.Timestamp.now(tz="Europe/Istanbul")
    cutoff = last_completed_weekday(now)
    # The actual provider session dates below remain authoritative.
    snapshots = load_monthly_snapshots()
    if snapshots.empty:
        raise ValueError("NO_PERSISTED_MONTHLY_SNAPSHOTS")
    snapshots["tarih"] = pd.to_datetime(snapshots["tarih"]).dt.normalize()
    latest_signal = snapshots["tarih"].max()
    # A snapshot generated today may be acted upon only from the NEXT session.
    current_month = cutoff[:7]
    latest = snapshots[snapshots["tarih"] == latest_signal]
    if (latest_signal.strftime("%Y-%m") != current_month
            or latest_signal.strftime("%Y-%m-%d") > cutoff):
        latest = None
    if state is None and latest is None:
        print("PAPER_WAITING_FOR_ELIGIBLE_MONTHLY_SNAPSHOT: " + cutoff)
        return
    if state is None:
        # Starting now, not replaying any previous trading day.
        state = new_state(cutoff)
    if cutoff > state["end_after"]:
        print("PAPER_WINDOW_COMPLETE")
        return
    # Daily replay only of sessions after the last committed observation.
    symbols = sorted(set(t for b in state["books"].values() for t in
                         (list(b["shares"]) + (b["pending"] or []))))
    if latest is not None:
        symbols = sorted(set(symbols) | set(latest["ticker"].astype(str)))
    if not symbols:
        raise ValueError("NO_TICKERS_TO_PRICE")
    start = state["last_day"] or state["start"]
    hist = download_history(symbols, start=start, min_rows=1)
    if not hist:
        raise ValueError("NO_DAILY_PRICES")
    sessions = sorted(set().union(*(set(pd.to_datetime(g.index).strftime("%Y-%m-%d")) for g in hist.values())))
    sessions = [d for d in sessions if d > (state["last_day"] or "0000-00-00")
                and d <= cutoff]
    if not sessions:
        print("NO_NEW_MARKET_SESSION")
        return
    # A snapshot is eligible only after its creation date, never retroactively.
    for day in sessions:
        bars = {}
        for ticker, g in hist.items():
            if pd.Timestamp(day) in g.index:
                r = g.loc[pd.Timestamp(day)]
                bars[ticker] = {"open": float(r["open"]), "close": float(r["close"])}
        signal = None
        if latest is not None and day > latest_signal.strftime("%Y-%m-%d"):
            if all(state["books"][f"{n}_1pct"]["last_signal_month"] != current_month for n in STRATEGIES):
                signal = {name: picks(latest, name) for name in STRATEGIES}
        state = step(state, day, bars, signal)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    os.replace(tmp, path)
    print(json.dumps({"status": "PAPER_ONLY", "day": state["last_day"],
                      "books": {k: {"nav": v["nav"], "max_drawdown_pct": v["max_drawdown_pct"]}
                                for k, v in state["books"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/paper_breakout/paper_state.json")
    run(parser.parse_args().output)
