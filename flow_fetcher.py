"""External market/flow data collection with explicit source confidence.

No fabricated foreign-ratio or HHI values are produced. When an external flow source
is unavailable, the feature remains missing and is excluded/down-weighted explicitly.
"""
from __future__ import annotations

import concurrent.futures
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import requests

TRADINGVIEW_URL = "https://scanner.tradingview.com/turkey/scan"
ISYATIRIM_URL = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.YatirimDanismanligi/PiyasaVerileri.aspx/GetHisseTakasData"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _num(v, default=np.nan):
    try:
        x = float(v)
        return x if np.isfinite(x) else default
    except Exception:
        return default


def get_bist_accumulation_data(limit: int = 450) -> pd.DataFrame:
    payload = {
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "Value.Traded", "operation": "greater", "right": 8000000},
        ],
        "columns": [
            "name", "close", "open", "high", "low", "volume", "change", "Value.Traded",
            "High.1M", "Low.1M", "relative_volume_10d_calc", "Perf.W", "Perf.1M",
            "Perf.Y", "return_on_equity_fq", "price_book_fq", "price_earnings_ttm",
            "market_cap_basic", "operating_margin",
        ],
        "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"},
        "range": [0, limit],
    }
    try:
        res = requests.post(TRADINGVIEW_URL, json=payload, headers=HEADERS, timeout=20)
        res.raise_for_status()
        data = res.json().get("data", [])
        rows: List[Dict] = []
        for item in data:
            d = item.get("d", [])
            if len(d) < 19:
                continue
            close = _num(d[1])
            rows.append({
                "ticker": d[0],
                "close": close,
                "open": _num(d[2], close),
                "high": _num(d[3], close),
                "low": _num(d[4], close),
                "volume": _num(d[5], np.nan),
                "change_%": _num(d[6], np.nan),
                "value_traded": _num(d[7], np.nan),
                "high_1m": _num(d[8], np.nan),
                "low_1m": _num(d[9], np.nan),
                "rvol": _num(d[10], np.nan),
                "perf_w": _num(d[11], np.nan),
                "perf_1m": _num(d[12], np.nan),
                "perf_y": _num(d[13], np.nan),
                "roe": _num(d[14], np.nan),
                "pb": _num(d[15], np.nan),
                "pe": _num(d[16], np.nan),
                "market_cap": _num(d[17], np.nan),
                "oper_margin": _num(d[18], np.nan),
            })
        df = pd.DataFrame(rows)
        if not df.empty:
            df["tarih"] = pd.Timestamp.now(tz="Europe/Istanbul").normalize().tz_localize(None)
        return df
    except Exception as exc:
        print(f"⚠️ TradingView veri hatası: {exc}")
        return pd.DataFrame()


def fetch_single_broker_flow(ticker: str) -> Tuple[str, float, float, str, float]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json; charset=utf-8",
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        res = requests.post(ISYATIRIM_URL, json={"hisseKodu": ticker}, headers=headers, timeout=4)
        res.raise_for_status()
        data = res.json().get("d", [])
        if not data:
            return ticker, np.nan, np.nan, "unavailable", 0.0

        shares = np.array([_num(x.get("Yuzde"), 0.0) for x in data[:15]], dtype=float)
        shares = shares[np.isfinite(shares) & (shares >= 0)]
        total = shares.sum()
        hhi = float(np.sum(((shares / total) * 100.0) ** 2)) if total > 0 else np.nan

        foreign_banks = {
            "CITIBANK YABANCI", "DEUTSCHE YABANCI", "HSBC YATIRIM",
            "YATIRIM FINANSMAN", "QNB FINANS"
        }
        foreign_ratio = float(sum(
            _num(x.get("Yuzde"), 0.0)
            for x in data
            if str(x.get("ALAN_ADI", "")).upper().strip() in foreign_banks
        ))

        if np.isfinite(hhi) or np.isfinite(foreign_ratio):
            return ticker, hhi, foreign_ratio, "isyatirim_takas", 100.0
        return ticker, np.nan, np.nan, "unavailable", 0.0
    except Exception:
        return ticker, np.nan, np.nan, "unavailable", 0.0


def fetch_all_data(limit: int = 450) -> pd.DataFrame:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Adaptive BIST Orderflow veri toplama başlıyor...")
    df_market = get_bist_accumulation_data(limit=limit)
    if df_market.empty:
        return df_market

    tickers = df_market["ticker"].tolist()
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(fetch_single_broker_flow, t) for t in tickers]
        for future in concurrent.futures.as_completed(futures):
            rows.append(future.result())

    flow = pd.DataFrame(rows, columns=[
        "ticker", "hhi_score", "foreign_ratio", "foreign_ratio_source", "foreign_ratio_confidence"
    ])
    out = df_market.merge(flow, on="ticker", how="left")
    out["flow_source_confidence"] = out["foreign_ratio_confidence"].fillna(0.0)
    out["tarih"] = pd.to_datetime(out["tarih"], errors="coerce").dt.normalize()
    return out
