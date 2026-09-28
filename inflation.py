"""Turkish CPI (TÜFE) — the yardstick the whole engine optimises against.

Free source chain (first that works wins, result cached in data/cpi_tr.csv):
  1. data/cpi_manual.csv          (optional, user-uploaded: tarih,cpi)
  2. TCMB EVDS series TP.FG.J0    (needs a FREE key: GitHub secret EVDS_API_KEY)
  3. FRED TURCPIALLMINMEI         (OECD monthly CPI index, keyless CSV)
  4. cached data/cpi_tr.csv
If a month is not yet published the real return for it is left unresolved;
the engine never invents inflation figures.
"""
from __future__ import annotations

import io
import os
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
import requests

import config as C

EVDS_URL = ("https://evds2.tcmb.gov.tr/service/evds/series={code}"
            "&startDate=01-01-2003&endDate=01-12-2035&type=json&frequency=5")
# TÜİK may re-base CPI (new series code). Add codes via env EVDS_CPI_SERIES="CODE1,CODE2".
EVDS_CODES = [c.strip() for c in os.environ.get("EVDS_CPI_SERIES", "TP.FG.J0").split(",") if c.strip()]
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=TURCPIALLMINMEI"


def _month(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return pd.Timestamp(year=t.year, month=t.month, day=1)


def _clean(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").dropna()
    s = s[s > 0]
    s.index = [_month(i) for i in s.index]
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def _from_manual() -> Optional[pd.Series]:
    if not os.path.exists(C.CPI_MANUAL_FILE):
        return None
    df = pd.read_csv(C.CPI_MANUAL_FILE)
    return _clean(pd.Series(df.iloc[:, 1].to_numpy(), index=pd.to_datetime(df.iloc[:, 0])))


def _from_evds() -> Optional[pd.Series]:
    key = os.environ.get("EVDS_API_KEY")
    if not key:
        return None
    out = None
    for code in EVDS_CODES:
        r = requests.get(EVDS_URL.format(code=code), headers={"key": key}, timeout=30)
        r.raise_for_status()
        field = code.replace(".", "_")
        idx, val = [], []
        for it in r.json().get("items", []):
            t = str(it.get("Tarih", ""))
            v = it.get(field)
            if not t or v in (None, ""):
                continue
            y, m = t.split("-")[:2]
            idx.append(pd.Timestamp(int(y), int(m), 1))
            val.append(float(v))
        if idx:
            s = _clean(pd.Series(val, index=idx))
            out = s if out is None else splice(out, s)
    return out


def splice(base: pd.Series, ext: pd.Series) -> pd.Series:
    """Extend `base` with the later months of `ext`, rescaled on the overlap (base change safe)."""
    if base is None or base.empty:
        return ext
    if ext is None or ext.empty or ext.index[-1] <= base.index[-1]:
        return base
    overlap = base.index.intersection(ext.index)
    if len(overlap) == 0:
        return base
    k = float(base[overlap[-1]] / ext[overlap[-1]])
    tail = ext[ext.index > base.index[-1]] * k
    return pd.concat([base, tail]).sort_index()


def _from_fred() -> Optional[pd.Series]:
    r = requests.get(FRED_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    date_col = df.columns[0]
    return _clean(pd.Series(df.iloc[:, 1].to_numpy(), index=pd.to_datetime(df[date_col])))


def _from_cache() -> Optional[pd.Series]:
    if not os.path.exists(C.CPI_CACHE_FILE):
        return None
    df = pd.read_csv(C.CPI_CACHE_FILE)
    return _clean(pd.Series(df["cpi"].to_numpy(), index=pd.to_datetime(df["tarih"])))


def load_cpi(allow_network: bool = True) -> Tuple[pd.Series, Dict]:
    tried = {}
    sources = [("manual", _from_manual)]
    if allow_network:
        sources += [("evds", _from_evds), ("fred", _from_fred)]
    sources += [("cache", _from_cache)]
    best, best_name = None, None
    for name, fn in sources:
        try:
            s = fn()
        except Exception as exc:
            tried[name] = f"error: {str(exc)[:120]}"
            continue
        if s is None or len(s) < 24:
            tried[name] = "unavailable"
            continue
        tried[name] = f"ok (last {s.index[-1]:%Y-%m})"
        if best is None:
            best, best_name = s, name
        elif s.index[-1] > best.index[-1]:
            best = splice(best, s)          # fresher months from a later source
            best_name = f"{best_name}+{name}"
    meta = {"source": best_name, "tried": tried}
    if best is None:
        meta["status"] = "UNAVAILABLE"
        return pd.Series(dtype=float), meta
    meta["last_month"] = str(best.index[-1].date())
    meta["status"] = "OK"
    if best_name != "cache":
        try:
            os.makedirs(C.DATA_DIR, exist_ok=True)
            pd.DataFrame({"tarih": best.index.strftime("%Y-%m-%d"), "cpi": best.values}).to_csv(C.CPI_CACHE_FILE, index=False)
        except Exception:
            pass
    return best, meta


def cpi_ratio(cpi: pd.Series, start, end) -> float:
    """CPI(end month)/CPI(start month); NaN if a month is not published."""
    if cpi is None or cpi.empty:
        return np.nan
    a, b = _month(start), _month(end)
    if a not in cpi.index or b not in cpi.index:
        return np.nan
    return float(cpi[b] / cpi[a])


def cpi_ratio_vec(cpi: pd.Series, starts, ends) -> np.ndarray:
    if cpi is None or cpi.empty:
        return np.full(len(starts), np.nan)
    m = cpi.to_dict()
    out = []
    for a, b in zip(starts, ends):
        va, vb = m.get(_month(a)), m.get(_month(b))
        out.append(vb / va if va and vb else np.nan)
    return np.asarray(out, float)


def inflation_stats(cpi: pd.Series) -> Dict:
    if cpi is None or len(cpi) < 13:
        return {"yoy_pct": None, "ann6m_pct": None, "expected_12m_pct": None}
    last = cpi.index[-1]
    yoy = (cpi.iloc[-1] / cpi.iloc[-13] - 1.0) * 100.0
    ann6 = ((cpi.iloc[-1] / cpi.iloc[-7]) ** 2 - 1.0) * 100.0 if len(cpi) >= 7 else yoy
    # Expected next-12m inflation: blend of trailing year and recent 6m pace.
    # Conservative (never below the recent pace blend) because the objective is
    # to beat inflation, so under-estimating it is the costly error.
    exp = max(0.5 * yoy + 0.5 * ann6, 0.0)
    return {"last_month": str(last.date()), "yoy_pct": round(float(yoy), 2),
            "ann6m_pct": round(float(ann6), 2), "expected_12m_pct": round(float(exp), 2)}
