"""Persistent state and files for the V3 real-return engine (atomic writes)."""
from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from typing import Dict

import numpy as np
import pandas as pd

import config as C

DEFAULT_STATE: Dict = {
    "version": C.ENGINE_VERSION,
    "strategy": C.STRATEGY_NAME,
    "objective": {"horizon_months": C.HORIZON_MONTHS, "primary": C.BENCHMARK_PRIMARY,
                  "secondary": C.BENCHMARK_SECONDARY},
    "portfolio": None,
    "model": {"champion_weights": {}, "champion_version": "prior-0", "previous_weights": {},
              "regime_weights": {}, "prior_source": "hand", "ic_stats": {}, "status": "BOOTSTRAP",
              "promotions": 0, "rollbacks": 0},
    "calibration": {"pct_cutoff": C.DEFAULT_PCT_CUTOFF, "bucket_table": [], "status": "BOOTSTRAP"},
    "regime": {"label": "UNKNOWN", "probs": {}, "p_risk_off": None, "degraded": True},
    "autonomy_guard": {},
    "inflation": {},
    "tv_fields": {},
    "sector_map": {},
    "performance": {},
    "last_rebalance": {},
    "last_run": {},
}


def ensure_dir():
    os.makedirs(C.DATA_DIR, exist_ok=True)


def _merge(base: Dict, inc: Dict) -> Dict:
    out = deepcopy(base)
    for k, v in (inc or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        v = float(o)
        return v if np.isfinite(v) else None
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return o.strftime("%Y-%m-%d")
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def _clean_nan(o):
    if isinstance(o, dict):
        return {k: _clean_nan(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean_nan(v) for v in o]
    if isinstance(o, float) and not np.isfinite(o):
        return None
    return o


def atomic_json_write(path: str, payload) -> bool:
    ensure_dir()
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_clean_nan(payload), f, ensure_ascii=False, indent=2, default=_default, allow_nan=False)
        os.replace(tmp, path)
        return True
    except Exception as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        print(f"❌ JSON yazma hatası ({path}): {exc}")
        return False


def atomic_csv_write(path: str, df: pd.DataFrame) -> bool:
    ensure_dir()
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".csv", dir=d)
    os.close(fd)
    try:
        df.to_csv(tmp, index=False)
        os.replace(tmp, path)
        return True
    except Exception as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        print(f"❌ CSV yazma hatası ({path}): {exc}")
        return False


def read_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"⚠️ JSON okuma hatası ({path}): {exc}")
        return None


def load_state() -> Dict:
    return _merge(DEFAULT_STATE, read_json(C.STATE_FILE) or {})


def save_state(state: Dict) -> bool:
    s = _merge(DEFAULT_STATE, state or {})
    s["version"] = C.ENGINE_VERSION
    return atomic_json_write(C.STATE_FILE, s)


def load_research_prior():
    return read_json(C.RESEARCH_PRIOR_FILE)


def _read_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception as exc:
        print(f"⚠️ CSV okuma hatası ({path}): {exc}")
        return pd.DataFrame()
    if "tarih" in df.columns:
        df["tarih"] = pd.to_datetime(df["tarih"], errors="coerce").dt.normalize()
    return df


def _snapshot_parts():
    import glob
    return sorted(glob.glob(os.path.join(C.MONTHLY_SNAPSHOT_DIR, "*.csv.gz")))


def load_monthly_snapshots() -> pd.DataFrame:
    """V3.14: aylık kesitler yıl başına sıkıştırılmış dosyalarda (data/monthly_snapshots/YYYY.csv.gz).
    Eski tek dosya (monthly_snapshots.csv) varsa o da okunur; bir sonraki kayıtta bölünüp silinir."""
    frames = []
    for p in _snapshot_parts():
        try:
            frames.append(pd.read_csv(p, low_memory=False))
        except Exception as exc:
            print(f"⚠️ CSV okuma hatası ({p}): {exc}")
    legacy = _read_csv(C.MONTHLY_SNAPSHOT_FILE)
    if not legacy.empty:
        frames.append(legacy)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["tarih"] = pd.to_datetime(df["tarih"], errors="coerce").dt.normalize()
    df["ticker"] = df["ticker"].astype(str)
    return df.drop_duplicates(["tarih", "ticker"], keep="last").reset_index(drop=True)


def save_monthly_snapshots(df: pd.DataFrame) -> bool:
    out = df.copy()
    out["tarih"] = pd.to_datetime(out["tarih"], errors="coerce").dt.normalize()
    out = out.dropna(subset=["tarih"]).drop_duplicates(["tarih", "ticker"], keep="last")
    dates = sorted(out["tarih"].unique())
    if len(dates) > C.MAX_MONTHLY_SNAPSHOTS:
        out = out[out["tarih"] >= dates[-C.MAX_MONTHLY_SNAPSHOTS]]
    num = out.select_dtypes(include=[np.number]).columns
    out[num] = out[num].round(5)
    os.makedirs(C.MONTHLY_SNAPSHOT_DIR, exist_ok=True)
    ok = True
    years = set()
    for y, g in out.groupby(out["tarih"].dt.year):
        years.add(int(y))
        g = g.sort_values(["tarih", "ticker"]).copy()
        g["tarih"] = g["tarih"].dt.strftime("%Y-%m-%d")
        path = os.path.join(C.MONTHLY_SNAPSHOT_DIR, f"{int(y)}.csv.gz")
        tmp = path + ".tmp"
        try:
            # mtime=0: içerik değişmeyen yıl dosyası her kayıtta aynı baytları üretir -> git'te değişiklik görünmez
            g.to_csv(tmp, index=False, compression={"method": "gzip", "mtime": 0})
            os.replace(tmp, path)
        except Exception as exc:
            ok = False
            print(f"❌ CSV yazma hatası ({path}): {exc}")
    for p in _snapshot_parts():             # pencereden düşen eski yıllar
        try:
            if int(os.path.basename(p).split(".")[0]) not in years:
                os.remove(p)
        except ValueError:
            pass
    if ok and os.path.exists(C.MONTHLY_SNAPSHOT_FILE):
        os.remove(C.MONTHLY_SNAPSHOT_FILE)  # eski tek dosya bölündü
    return ok


def clear_monthly_snapshots():
    import shutil
    shutil.rmtree(C.MONTHLY_SNAPSHOT_DIR, ignore_errors=True)
    if os.path.exists(C.MONTHLY_SNAPSHOT_FILE):
        os.remove(C.MONTHLY_SNAPSHOT_FILE)


def append_rows(path: str, rows) -> bool:
    if rows is None or len(rows) == 0:
        return True
    new = pd.DataFrame(rows)
    old = _read_csv(path)
    if not old.empty and "tarih" in old.columns:
        old["tarih"] = old["tarih"].dt.strftime("%Y-%m-%d")
    out = pd.concat([old, new], ignore_index=True) if not old.empty else new
    if path == C.NAV_FILE and "tarih" in out.columns:
        out = out.drop_duplicates(subset=["tarih"], keep="last")
    return atomic_csv_write(path, out)


def load_nav() -> pd.DataFrame:
    return _read_csv(C.NAV_FILE)


def load_trade_log() -> pd.DataFrame:
    return _read_csv(C.TRADE_LOG_FILE)
