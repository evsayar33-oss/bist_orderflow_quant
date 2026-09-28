"""Persistent state, ledgers and atomic file IO for Meta-Engine V2."""
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
    "model": {
        "champion_weights": {},
        "champion_version": "prior-0",
        "previous_weights": {},
        "previous_version": None,
        "regime_weights": {},
        "prior_source": "hand",
        "ic_stats": {},
        "composite_ic_live": {},
        "challenger": {},
        "promotions": 0,
        "rollbacks": 0,
        "last_fit": None,
        "status": "BOOTSTRAP",
    },
    "calibration": {
        "pct_cutoff": C.DEFAULT_PCT_CUTOFF,
        "bucket_table": [],
        "source": "default",
        "status": "BOOTSTRAP",
    },
    "regime": {
        "params": None,
        "last_fit": None,
        "label": "UNKNOWN",
        "probs": {},
        "p_risk_off": None,
        "exp_mkt_5d_pct": 0.0,
        "argmax_history": [],
        "degraded": True,
        "source": "none",
    },
    "autonomy_guard": {},
    "takas": {"enabled": True, "zero_runs": 0, "runs_since_check": 0, "last_coverage": 0.0},
    "data_quality": {},
    "performance": {},
    "sector_map": {},
    "last_run": {},
}

SNAPSHOT_RAW_COLS = [
    "tarih", "ticker", "open", "high", "low", "close", "volume", "change_pct",
    "value_traded", "rvol", "perf_w", "perf_1m", "perf_3m", "high_1m", "low_1m",
    "atr", "market_cap", "sector", "takas_conc", "takas_conf",
]

LEDGER_COLS = [
    "signal_date", "ticker", "status", "composite", "composite_pct", "exp_net_pct",
    "regime_label", "p_risk_off", "model_version", "atr_pct", "stop_dist_pct", "size_pct",
    "signal_close", "entry_date", "entry_price", "stop_price", "target_price", "breakeven_armed",
    "expiry_date", "sessions_held", "last_date", "last_price", "mfe_pct", "mae_pct",
    "exit_date", "exit_price", "exit_reason", "gross_ret_pct", "net_ret_pct",
]


def ensure_dir() -> None:
    os.makedirs(C.DATA_DIR, exist_ok=True)


def _merge(base: Dict, incoming: Dict) -> Dict:
    out = deepcopy(base)
    for k, v in (incoming or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o)
        return v if np.isfinite(v) else None
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.strftime("%Y-%m-%d")
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def atomic_json_write(path: str, payload: Dict) -> bool:
    ensure_dir()
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=_json_default)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as exc:  # pragma: no cover
        try:
            os.unlink(tmp)
        except OSError:
            pass
        print(f"❌ JSON yazma hatası ({path}): {exc}")
        return False


def atomic_csv_write(path: str, df: pd.DataFrame) -> bool:
    ensure_dir()
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".csv", dir=directory)
    os.close(fd)
    try:
        df.to_csv(tmp, index=False)
        os.replace(tmp, path)
        return True
    except Exception as exc:  # pragma: no cover
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
    raw = read_json(C.STATE_FILE)
    return _merge(DEFAULT_STATE, raw or {})


def save_state(state: Dict) -> bool:
    state = _merge(DEFAULT_STATE, state or {})
    state["version"] = C.ENGINE_VERSION
    return atomic_json_write(C.STATE_FILE, state)


def load_research_prior():
    return read_json(C.RESEARCH_PRIOR_FILE)


# ------------------------------------------------------------------ snapshots
def load_snapshots() -> pd.DataFrame:
    if not os.path.exists(C.SNAPSHOT_FILE):
        return pd.DataFrame()
    try:
        df = pd.read_csv(C.SNAPSHOT_FILE, low_memory=False)
    except Exception as exc:
        print(f"⚠️ Snapshot okuma hatası: {exc}")
        return pd.DataFrame()
    if df.empty or "tarih" not in df.columns:
        return pd.DataFrame()
    df["tarih"] = pd.to_datetime(df["tarih"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["tarih", "ticker"])
    df["ticker"] = df["ticker"].astype(str)
    return df


def save_snapshots(df: pd.DataFrame) -> bool:
    if df is None or df.empty:
        return False
    out = df.copy()
    out["tarih"] = pd.to_datetime(out["tarih"]).dt.normalize()
    dates = sorted(out["tarih"].unique())
    if len(dates) > C.MAX_SNAPSHOT_SESSIONS:
        out = out[out["tarih"] >= dates[-C.MAX_SNAPSHOT_SESSIONS]]
    out = out.sort_values(["tarih", "ticker"])
    num = out.select_dtypes(include=[np.number]).columns
    out[num] = out[num].round(5)
    out["tarih"] = out["tarih"].dt.strftime("%Y-%m-%d")
    return atomic_csv_write(C.SNAPSHOT_FILE, out)


def append_snapshot(existing: pd.DataFrame, today: pd.DataFrame) -> pd.DataFrame:
    if existing is None or existing.empty:
        return today.copy()
    d = pd.Timestamp(today["tarih"].iloc[0]).normalize()
    keep = existing[existing["tarih"] != d]
    return pd.concat([keep, today], ignore_index=True)


# ------------------------------------------------------------------ ledger
def load_ledger() -> pd.DataFrame:
    if not os.path.exists(C.LEDGER_FILE):
        return pd.DataFrame(columns=LEDGER_COLS)
    try:
        df = pd.read_csv(C.LEDGER_FILE)
    except Exception as exc:
        print(f"⚠️ Ledger okuma hatası: {exc}")
        return pd.DataFrame(columns=LEDGER_COLS)
    for c in LEDGER_COLS:
        if c not in df.columns:
            df[c] = np.nan
    for c in ("signal_date", "entry_date", "expiry_date", "last_date", "exit_date"):
        df[c] = pd.to_datetime(df[c], errors="coerce").dt.normalize()
    df["ticker"] = df["ticker"].astype(str)
    df["status"] = df["status"].astype(str)
    df["exit_reason"] = df["exit_reason"].astype(object)
    df["regime_label"] = df["regime_label"].astype(object)
    df["model_version"] = df["model_version"].astype(object)
    return df[LEDGER_COLS]


def save_ledger(df: pd.DataFrame) -> bool:
    out = df.copy()
    for c in LEDGER_COLS:
        if c not in out.columns:
            out[c] = np.nan
    out = out[LEDGER_COLS]
    for c in ("signal_date", "entry_date", "expiry_date", "last_date", "exit_date"):
        out[c] = pd.to_datetime(out[c], errors="coerce").dt.strftime("%Y-%m-%d")
    return atomic_csv_write(C.LEDGER_FILE, out)
