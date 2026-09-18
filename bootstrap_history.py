"""Bootstrap lifecycle signals from real historical OHLCV only.

This module intentionally FAILS CLOSED when remote historical data is unavailable.
It never creates synthetic market data.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List

import numpy as np
import pandas as pd
import yfinance as yf

from state_manager import LIFECYCLE_LOG_FILE, load_lifecycle_signals, save_lifecycle_signals

BOOTSTRAP_TICKERS = [
    "AKBNK.IS", "ASELS.IS", "BIMAS.IS", "EREGL.IS", "FROTO.IS", "GARAN.IS", "ISCTR.IS", "KCHOL.IS",
    "KOZAL.IS", "ODAS.IS", "PETKM.IS", "SAHOL.IS", "SISE.IS", "TCELL.IS", "THYAO.IS", "TOASO.IS",
    "TUPRS.IS", "YKBNK.IS", "ARCLK.IS", "CCOLA.IS", "DOAS.IS", "LOGO.IS", "MIATK.IS", "GESAN.IS",
    "EUPWR.IS", "KFEIN.IS", "PAPIL.IS", "ARDYZ.IS", "BANVT.IS", "ALFAS.IS", "ASTOR.IS", "VESBE.IS",
]


def run_historical_bootstrap(start="2022-01-01", end=None, max_symbols=33) -> bool:
    end = end or datetime.now().strftime("%Y-%m-%d")
    tickers = BOOTSTRAP_TICKERS[:max_symbols]
    try:
        raw = yf.download(tickers, start=start, end=end, interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True)
    except Exception as exc:
        print(f"⚠️ Bootstrap veri çekemedi: {exc}")
        return False
    if raw is None or raw.empty:
        print("⚠️ Gerçek tarihsel veri yok; bootstrap iptal edildi.")
        return False

    rows = []
    for ticker in tickers:
        try:
            if len(tickers) == 1:
                g = raw.copy()
            else:
                if ticker not in raw.columns.get_level_values(0):
                    continue
                g = raw[ticker].copy()
            g = g.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}).dropna(subset=["close", "high", "low"])
            if len(g) < 260:
                continue
            g["date"] = pd.to_datetime(g.index).normalize()
            g["rvol"] = g["volume"] / g["volume"].rolling(20).mean()
            g["high_1m"] = g["high"].rolling(21).max().shift(1)
            g["low_1m"] = g["low"].rolling(21).min().shift(1)
            g["perf_w"] = g["close"].pct_change(5) * 100
            g["perf_1m"] = g["close"].pct_change(21) * 100
            g = g.dropna(subset=["rvol", "high_1m", "low_1m"])
            for i in range(max(260, 21), len(g) - 10):
                row = g.iloc[i]
                span = float(row.high_1m - row.low_1m)
                if span <= 0:
                    continue
                range_position = float((row.close - row.low_1m) / span * 100)
                dist_support = float((row.close - row.low_1m) / row.low_1m * 100) if row.low_1m > 0 else 999.0
                # Research-only pre-move trigger; no future values are used in features.
                pre_score = (
                    float(np.clip(100 - abs(range_position - 25) * 2, 0, 100)) * 0.45
                    + float(np.clip(row.rvol / 2 * 100, 0, 100)) * 0.30
                    + float(np.clip(row.perf_1m + 20, 0, 100)) * 0.10
                    + float(np.clip(100 - dist_support * 3, 0, 100)) * 0.15
                )
                if pre_score < 70:
                    continue
                future = g.iloc[i + 1 : i + 11]
                entry = float(row.close)
                ret3 = np.nan if len(future) < 3 else float((future.iloc[2].close / entry - 1) * 100)
                ret5 = np.nan if len(future) < 5 else float((future.iloc[4].close / entry - 1) * 100)
                ret10 = np.nan if len(future) < 10 else float((future.iloc[9].close / entry - 1) * 100)
                mfe = float((future.high.max() / entry - 1) * 100)
                mae = float((future.low.min() / entry - 1) * 100)
                rows.append({
                    "tarih": row.date, "ticker": ticker.replace(".IS", ""), "entry_price": entry,
                    "pre_move_score": round(pre_score, 1), "flow_score": round(np.clip(row.rvol * 45, 0, 100), 1),
                    "resilience_score": round(np.clip(50 + row.perf_w * 1.5, 0, 100), 1),
                    "regime_fit_score": 60.0, "quality_score": 75.0, "risk_score": round(np.clip(50 - mae * 1.2, 0, 100), 1),
                    "data_quality": 90.0, "market_regime": "BOOTSTRAP", "regime_confidence": 50.0,
                    "model_version": "bootstrap-v1", "initial_stop_price": round(entry * 0.92, 4),
                    "current_stop_price": round(entry * 0.92, 4), "target_price": round(entry * 1.20, 4),
                    "ret_t1": np.nan if len(future) < 1 else float((future.iloc[0].close / entry - 1) * 100),
                    "ret_t3": ret3, "ret_t5": ret5, "ret_t10": ret10,
                    "max_favorable_excursion": mfe, "max_adverse_excursion": mae,
                    "peak_price": float(future.high.max()), "trough_price": float(future.low.min()), "outcome": "BOOTSTRAP_RESOLVED" if np.isfinite(ret3) else "PENDING",
                })
        except Exception as exc:
            print(f"⚠️ Bootstrap {ticker} atlandı: {exc}")

    if not rows:
        print("⚠️ Yeterli gerçek geçmiş sinyali üretilemedi; bootstrap kaydedilmedi.")
        return False

    new = pd.DataFrame(rows)
    old = load_lifecycle_signals()
    if not old.empty:
        combined = pd.concat([old, new], ignore_index=True)
        combined = combined.drop_duplicates(subset=["tarih", "ticker"], keep="last")
    else:
        combined = new
    save_lifecycle_signals(combined)
    print(f"✅ Gerçek tarihsel bootstrap tamamlandı: {len(new)} sinyal.")
    return True


if __name__ == "__main__":
    run_historical_bootstrap()
