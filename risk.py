"""V3.16 — Portföy seviyesinde koruma (ana kasayı derin düşüşlerden korur).

Hisse bazındaki kurallar tek tek hisseleri korur; bu modül portföyün TOPLAM hisse oranını ayarlar.
Sonuç `state["risk_exposure"]` (0-1): aylık planda hedef ağırlıklar bu oranla çarpılır, kalan nakitte faiz alır.

RISK_MODE:
  None         kapalı (her zaman %100 hisse)
  "dd_brake"   portföy zirveden RISK_DD_TRIGGER_PCT düşünce hisse oranı RISK_DD_EXPOSURE'a iner;
               düşüş tetik eşiğinin yarısına gerileyince %100'e döner (gidip gelmeyi önler)
  "trend_usd"  BIST100 dolar bazında RISK_TREND_MONTHS aylık ortalamasının altındaysa hisse oranı RISK_TREND_EXPOSURE
  "vol_target" hisse oranı = RISK_VOL_TARGET_PCT / son 3 ayın yıllık oynaklığı (en fazla %100, en az RISK_MIN_EXPOSURE)
  "combo"      dd_brake ve trend_usd birlikte (düşük olan geçerli)
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

import config as C


def _cfg(name, default):
    return getattr(C, name, default)


def _dd_brake(nav: pd.Series, mem: Dict) -> float:
    trig = float(_cfg("RISK_DD_TRIGGER_PCT", 20.0))
    dd = (nav.iloc[-1] / nav.cummax().iloc[-1] - 1) * 100
    on = bool(mem.get("dd_on"))
    if not on and dd <= -trig:
        on = True
    elif on and dd > -trig / 2:
        on = False
    mem["dd_on"] = on
    return float(_cfg("RISK_DD_EXPOSURE", 0.5)) if on else 1.0


def _trend_usd(idx_usd: Optional[pd.Series]) -> float:
    if idx_usd is None:
        return 1.0
    m = idx_usd.dropna().resample("ME").last().dropna()
    k = int(_cfg("RISK_TREND_MONTHS", 10))
    if len(m) < k:
        return 1.0
    return float(_cfg("RISK_TREND_EXPOSURE", 0.5)) if m.iloc[-1] < m.iloc[-k:].mean() else 1.0


def _vol_target(nav: pd.Series) -> float:
    r = nav.pct_change().dropna().iloc[-63:]
    if len(r) < 40:
        return 1.0
    v = float(r.std() * np.sqrt(252) * 100)
    return float(np.clip(_cfg("RISK_VOL_TARGET_PCT", 25.0) / max(v, 1e-9), _cfg("RISK_MIN_EXPOSURE", 0.3), 1.0))


def exposure(nav: Optional[pd.Series], idx_usd: Optional[pd.Series], mem: Dict) -> float:
    """nav: günlük portföy değeri (bugüne kadar). idx_usd: BIST100 / USDTRY. mem: kalıcı durum (gidip gelme önleme)."""
    mode = _cfg("RISK_MODE", None)
    if not mode or nav is None or len(nav.dropna()) < 2:
        return 1.0
    nav = nav.dropna()
    if mode == "dd_brake":
        x = _dd_brake(nav, mem)
    elif mode == "trend_usd":
        x = _trend_usd(idx_usd)
    elif mode == "vol_target":
        x = _vol_target(nav)
    elif mode == "combo":
        x = min(_dd_brake(nav, mem), _trend_usd(idx_usd))
    else:
        x = 1.0
    return round(float(np.clip(x, 0.0, 1.0)), 4)
