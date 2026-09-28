"""Free market-data collection for Meta-Engine V2.

Sources (all free, no API key):
* TradingView public scanner (end-of-day snapshot, run after 18:10 TR close).
* Is Yatirim takas endpoint (best effort, optional). It is frequently
  unavailable; the engine measures coverage and automatically ignores the
  feature when coverage is too low. Nothing is ever fabricated.
"""
from __future__ import annotations

import concurrent.futures
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import requests

import config as C

TRADINGVIEW_URL = "https://scanner.tradingview.com/turkey/scan"
ISYATIRIM_URL = (
    "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.YatirimDanismanligi/"
    "PiyasaVerileri.aspx/GetHisseTakasData"
)
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# TradingView column -> engine column
FULL_COLUMNS = {
    "name": "ticker",
    "close": "close",
    "open": "open",
    "high": "high",
    "low": "low",
    "volume": "volume",
    "change": "change_pct",
    "Value.Traded": "value_traded",
    "relative_volume_10d_calc": "rvol",
    "Perf.W": "perf_w",
    "Perf.1M": "perf_1m",
    "Perf.3M": "perf_3m",
    "High.1M": "high_1m",
    "Low.1M": "low_1m",
    "ATR": "atr",
    "market_cap_basic": "market_cap",
    "sector": "sector",
}
CORE_COLUMNS = [
    "name", "close", "open", "high", "low", "volume", "change", "Value.Traded",
    "relative_volume_10d_calc", "Perf.W", "Perf.1M", "High.1M", "Low.1M",
]


def _num(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def _scan(columns: List[str], limit: int) -> pd.DataFrame:
    payload = {
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "Value.Traded", "operation": "greater", "right": 1_000_000},
        ],
        "columns": columns,
        "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"},
        "range": [0, limit],
    }
    res = requests.post(TRADINGVIEW_URL, json=payload, headers=HEADERS, timeout=25)
    res.raise_for_status()
    rows: List[Dict] = []
    for item in res.json().get("data", []):
        d = item.get("d", [])
        if len(d) != len(columns):
            continue
        rec = {}
        for col, val in zip(columns, d):
            key = FULL_COLUMNS.get(col, col)
            rec[key] = val if key in ("ticker", "sector") else _num(val)
        rows.append(rec)
    return pd.DataFrame(rows)


def fetch_market_snapshot(limit: int = C.SCAN_LIMIT) -> Tuple[pd.DataFrame, Dict]:
    """Returns (snapshot, meta). Falls back to a core column set if the
    extended request is rejected. Missing extended fields stay NaN."""
    meta = {"source": "tradingview", "column_set": None, "error": None}
    full = list(FULL_COLUMNS.keys())
    attempts = [
        ("full", full),
        ("no_sector", [c for c in full if c != "sector"]),
        ("no_sector_atr", [c for c in full if c not in ("sector", "ATR")]),
        ("core", CORE_COLUMNS),
    ]
    df = pd.DataFrame()
    errors = []
    for name, cols in attempts:
        try:
            df = _scan(cols, limit)
            if not df.empty:
                meta["column_set"] = name
                break
        except Exception as exc:
            errors.append(f"{name}:{str(exc)[:120]}")
    if errors:
        meta["error"] = " | ".join(errors)
    if df.empty:
        print(f"⚠️ TradingView veri hatası: {meta['error']}")
        return pd.DataFrame(), meta
    if df.empty:
        return df, meta
    for col in FULL_COLUMNS.values():
        if col not in df.columns:
            df[col] = np.nan if col not in ("ticker", "sector") else None
    df["ticker"] = df["ticker"].astype(str)
    df["tarih"] = pd.Timestamp.now(tz=C.MARKET_TZ).normalize().tz_localize(None)
    return df, meta


def _fetch_takas_one(ticker: str) -> Tuple[str, float, float]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json; charset=utf-8",
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        res = requests.post(ISYATIRIM_URL, json={"hisseKodu": ticker}, headers=headers, timeout=4)
        res.raise_for_status()
        data = res.json().get("d", [])
        shares = np.array([_num(x.get("Yuzde")) for x in data], dtype=float)
        shares = shares[np.isfinite(shares) & (shares >= 0)]
        if shares.size == 0:
            return ticker, np.nan, 0.0
        top5 = float(np.sort(shares)[::-1][:5].sum())
        return ticker, top5, 100.0
    except Exception:
        return ticker, np.nan, 0.0


def fetch_takas(tickers: List[str], max_workers: int = 16) -> pd.DataFrame:
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for r in ex.map(_fetch_takas_one, tickers):
            rows.append(r)
    return pd.DataFrame(rows, columns=["ticker", "takas_conc", "takas_conf"])


def fetch_all_data(state: Dict, limit: int = C.SCAN_LIMIT) -> Tuple[pd.DataFrame, Dict]:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] EOD BIST snapshot toplanıyor...")
    df, meta = fetch_market_snapshot(limit)
    if df.empty:
        return df, meta

    tk = state.setdefault("takas", {})
    run_takas = bool(tk.get("enabled", True))
    if not run_takas:
        tk["runs_since_check"] = int(tk.get("runs_since_check", 0)) + 1
        if tk["runs_since_check"] >= 10:          # re-probe every ~2 weeks
            run_takas = True
    df["takas_conc"] = np.nan
    df["takas_conf"] = 0.0
    if run_takas:
        t = fetch_takas(df["ticker"].tolist())
        df = df.drop(columns=["takas_conc", "takas_conf"]).merge(t, on="ticker", how="left")
        cov = float(pd.to_numeric(df["takas_conc"], errors="coerce").notna().mean())
        tk["last_coverage"] = round(cov, 4)
        tk["runs_since_check"] = 0
        if cov < 0.30:
            tk["zero_runs"] = int(tk.get("zero_runs", 0)) + 1
            if tk["zero_runs"] >= 5:
                tk["enabled"] = False
        else:
            tk["zero_runs"] = 0
            tk["enabled"] = True
    meta["takas_coverage"] = tk.get("last_coverage", 0.0)
    return df, meta
