"""Real-data-only walk-forward optimizer for Adaptive BIST Orderflow Meta-Engine V1."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from typing import Dict, List

import numpy as np
import pandas as pd
import yfinance as yf

from backtest_validator import performance_metrics, validate_candidate, stability_check

UNIVERSE = [
    "AKBNK.IS", "ASELS.IS", "BIMAS.IS", "EREGL.IS", "FROTO.IS", "GARAN.IS", "ISCTR.IS", "KCHOL.IS",
    "KOZAL.IS", "ODAS.IS", "PETKM.IS", "SAHOL.IS", "SISE.IS", "TCELL.IS", "THYAO.IS", "TOASO.IS",
    "TUPRS.IS", "YKBNK.IS", "ARCLK.IS", "CCOLA.IS", "DOAS.IS", "LOGO.IS", "MIATK.IS", "GESAN.IS",
    "EUPWR.IS", "KFEIN.IS", "PAPIL.IS", "ARDYZ.IS", "BANVT.IS", "ALFAS.IS", "ASTOR.IS", "VESBE.IS",
]


def download_history(tickers: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
    try:
        raw = yf.download(tickers, start=start, end=end, interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True)
    except Exception as exc:
        raise RuntimeError(f"Gerçek tarihsel veri alınamadı: {exc}") from exc
    if raw is None or raw.empty:
        raise RuntimeError("Gerçek tarihsel veri alınamadı; backtest durduruldu.")

    out = {}
    for ticker in tickers:
        try:
            if len(tickers) > 1 and ticker in raw.columns.get_level_values(0):
                g = raw[ticker].copy()
            elif len(tickers) == 1:
                g = raw.copy()
            else:
                continue
            g = g.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
            g = g.dropna(subset=["open", "high", "low", "close", "volume"])
            if len(g) >= 280:
                out[ticker] = g
        except Exception:
            continue

    if len(out) < max(5, len(tickers) // 4):
        raise RuntimeError(f"Gerçek veri kapsamı yetersiz: {len(out)} hisse.")
    return out


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    g = df.copy()
    g.index = pd.to_datetime(g.index).tz_localize(None)
    g["rvol"] = g["volume"] / g["volume"].rolling(20).mean()
    g["high_1m"] = g["high"].rolling(21).max().shift(1)
    g["low_1m"] = g["low"].rolling(21).min().shift(1)
    g["perf_w"] = g["close"].pct_change(5) * 100
    g["perf_1m"] = g["close"].pct_change(21) * 100
    return g.dropna(subset=["rvol", "high_1m", "low_1m", "perf_w", "perf_1m"])


def simulate_prepared(g: pd.DataFrame, threshold: float, start_date=None, end_date=None, ticker="") -> pd.DataFrame:
    if g is None or g.empty:
        return pd.DataFrame()
    if start_date is not None:
        g = g[g.index >= pd.Timestamp(start_date)]
    if end_date is not None:
        g = g[g.index <= pd.Timestamp(end_date)]
    if len(g) < 20:
        return pd.DataFrame()

    trades = []
    for i in range(len(g) - 3):
        row = g.iloc[i]
        span = float(row.high_1m - row.low_1m)
        if span <= 0 or row.low_1m <= 0:
            continue
        range_pos = (row.close - row.low_1m) / span * 100
        dist = (row.close - row.low_1m) / row.low_1m * 100
        score = (
            np.clip(100 - abs(range_pos - 25) * 2, 0, 100) * 0.45
            + np.clip(row.rvol / 2 * 100, 0, 100) * 0.30
            + np.clip(100 - dist * 3, 0, 100) * 0.15
            + np.clip(row.perf_w + 20, 0, 100) * 0.10
        )
        if score < threshold:
            continue
        future = g.iloc[i + 1 : i + 4]
        if len(future) < 3:
            continue
        entry = float(row.close)
        exit_p = float(future.iloc[-1].close)
        pnl = (exit_p / entry - 1) * 100
        trades.append({
            "entry_date": row.name,
            "exit_date": future.iloc[-1].name,
            "pnl_pct": pnl,
            "score": float(score),
            "ticker": ticker.replace(".IS", ""),
        })
    return pd.DataFrame(trades)


def collect_trades(data, threshold, start_date=None, end_date=None):
    pieces = []
    for ticker, raw in data.items():
        g = prepare(raw)
        t = simulate_prepared(g, threshold, start_date, end_date, ticker)
        if not t.empty:
            pieces.append(t)
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()


def choose_threshold(train_trades: pd.DataFrame, thresholds) -> Dict:
    candidates = []
    for th in thresholds:
        t = train_trades[train_trades["score"] >= th].copy() if not train_trades.empty else pd.DataFrame()
        metrics = validate_candidate(t, min_trades=20)
        metrics["threshold"] = th
        candidates.append(metrics)
    valid = [x for x in candidates if x.get("passed")]
    if valid:
        return max(valid, key=lambda x: (x["profit_factor"], x["avg_pnl"]))
    return max(candidates, key=lambda x: (x.get("profit_factor", 0.0), x.get("avg_pnl", 0.0)))


def run_walk_forward(data, thresholds=(70, 75, 80, 85, 90), min_train_days=250, test_days=90):
    all_dates = sorted(set(d for g in data.values() for d in pd.to_datetime(g.index).tz_localize(None)))
    if len(all_dates) < min_train_days + test_days:
        raise RuntimeError("Walk-forward için yeterli gerçek işlem günü yok.")

    folds = []
    cursor = min_train_days
    while cursor < len(all_dates):
        train_start = all_dates[0]
        train_end = all_dates[cursor - 1]
        test_end_idx = min(cursor + test_days - 1, len(all_dates) - 1)
        test_start = all_dates[cursor]
        test_end = all_dates[test_end_idx]
        train = collect_trades(data, threshold=0, start_date=train_start, end_date=train_end)
        chosen = choose_threshold(train, thresholds)
        test = collect_trades(data, threshold=chosen["threshold"], start_date=test_start, end_date=test_end)
        test_metrics = performance_metrics(test)
        folds.append({
            "train_start": str(train_start.date()), "train_end": str(train_end.date()),
            "test_start": str(test_start.date()), "test_end": str(test_end.date()),
            "selected_threshold": chosen["threshold"], "train": chosen, "test": test_metrics,
        })
        cursor = test_end_idx + 1

    oos_pieces = []
    for f in folds:
        t = collect_trades(data, threshold=f["selected_threshold"], start_date=f["test_start"], end_date=f["test_end"])
        if not t.empty:
            oos_pieces.append(t)
    oos = pd.concat(oos_pieces, ignore_index=True) if oos_pieces else pd.DataFrame()
    return folds, performance_metrics(oos), stability_check([f["test"] for f in folds])


def run(start: str, end: str, save: bool = False):
    data = download_history(UNIVERSE, start, end)
    folds, oos_metrics, stability = run_walk_forward(data)
    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "period": f"{start}..{end}",
        "universe_size": len(data),
        "folds": folds,
        "oos_metrics": oos_metrics,
        "stability": stability,
        "synthetic_data_used": False,
        "data_source": "Yahoo Finance OHLCV",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if save:
        with open("backtest_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2022-01-01")
    parser.add_argument("--end-date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    run(args.start_date, args.end_date, args.save)


if __name__ == "__main__":
    main()
