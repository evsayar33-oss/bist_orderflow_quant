"""V3.15 — Kasa önerisi, her gün güncel fiyat ve işlem hacminden yeniden hesaplanır.

Her ay kasanın ~1/(TRANCHE_N x TRANCHE_MONTHS)'u ile bir hisse alınır ve BIST'te en küçük işlem 1 adettir.
  asgari   : aday hisselerin %90'ında 1 adetlik yuvarlama hatası %2'yi geçmez
  önerilen : aday hisselerin hepsinde yuvarlama hatası %2'yi geçmez
  azami    : bir alım, adayların medyan günlük işlem hacminin %5'ini geçmez
Adaylar = likidite tabanını geçen, en yüksek puanlı CANDIDATES hisse (sistemin önümüzdeki aylarda alabileceği hisseler).
Fiyatlar TL olduğu için tutarlar enflasyonla kendiliğinden güncellenir.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

import config as C

CANDIDATES = 30
MAX_ROUNDING = 0.02          # 1 adetlik yuvarlama hatası alım tutarının en fazla %2'si
MAX_PARTICIPATION = 0.05     # bir alım günlük işlem hacminin en fazla %5'i


def _nice(v: float) -> float:
    """Okunur yuvarlama: 10 bin / 1 milyon TL basamağına yukarı."""
    step = 1_000_000 if v >= 10_000_000 else 10_000
    return float(np.ceil(v / step) * step)


def advise(frame: pd.DataFrame, prices: Optional[Dict[str, float]] = None,
           liq_floor: Optional[float] = None) -> Optional[Dict]:
    if frame is None or frame.empty or "composite" not in frame:
        return None
    f = frame.copy()
    floor = float(liq_floor or C.MIN_MEDIAN_VALUE_TRADED_TL)
    mv = pd.to_numeric(f.get("med_value_traded"), errors="coerce")
    f = f[mv >= floor].sort_values("composite", ascending=False).head(CANDIDATES)
    if len(f) < 5:
        return None
    px = f["ticker"].map(prices or {}) if prices else pd.Series(np.nan, index=f.index)
    px = pd.to_numeric(px, errors="coerce").fillna(pd.to_numeric(f.get("close_adj"), errors="coerce")).dropna()
    adv = pd.to_numeric(f["med_value_traded"], errors="coerce").dropna()
    if px.empty or adv.empty:
        return None
    slots = int(C.TRANCHE_N) * int(C.TRANCHE_MONTHS)
    lo = _nice(float(px.quantile(0.9)) / MAX_ROUNDING * slots)
    rec = max(_nice(float(px.max()) / MAX_ROUNDING * slots), lo)
    hi = _nice(float(adv.median()) * MAX_PARTICIPATION * slots)
    return {"min_tl": lo, "recommended_tl": rec, "max_tl": max(hi, rec), "candidates": int(len(f)),
            "median_price_tl": round(float(px.median()), 2), "max_price_tl": round(float(px.max()), 2),
            "median_adv_tl": round(float(adv.median()), 0)}


def current(state: Dict) -> Dict:
    """Sitenin göstereceği tutarlar: son hesaplanan, yoksa config'teki sabit değerler."""
    a = (state or {}).get("capital_advice") or {}
    return {"min_tl": a.get("min_tl") or C.MIN_CAPITAL_TL,
            "recommended_tl": a.get("recommended_tl") or C.RECOMMENDED_CAPITAL_TL,
            "max_tl": a.get("max_tl") or C.MAX_CAPITAL_TL, "date": a.get("date"), "dynamic": bool(a)}
