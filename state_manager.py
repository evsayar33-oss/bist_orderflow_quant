"""Persistent state, schema migration and atomic writes for Meta-Engine V1."""
from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from typing import Dict

import pandas as pd
import numpy as np

STATE_FILE = "longterm_ai_state.json"
WEIGHTS_FILE = "model_weights.json"
LIFECYCLE_LOG_FILE = "signals_lifecycle.csv"
SIGNAL_LOG_FILE = "signals_log.csv"

DEFAULT_STATE = {
    "version": "5.0.0",
    "strategy": "ADAPTIVE_BIST_ORDERFLOW_META_ENGINE_V1",
    "market_regime": "NEUTRAL",
    "regime_confidence": 50.0,
    "weights": {
        "pre_move": 0.40,
        "flow": 0.25,
        "resilience": 0.15,
        "regime_fit": 0.10,
        "quality": 0.10,
    },
    "shadow_weights": {
        "pre_move": 0.40,
        "flow": 0.25,
        "resilience": 0.15,
        "regime_fit": 0.10,
        "quality": 0.10,
    },
    "regime_weights": {},
    "learning_params": {
        "learning_rate": 0.08,
        "min_sample_size": 30,
        "shadow_min_sample": 20,
        "promotion_margin": 0.05,
        "max_weight_shift": 0.08,
        "min_weight": 0.05,
        "max_weight": 0.55,
        "cooldown_days": 5,
    },
    "validation": {
        "active_model_version": "champion-1",
        "shadow_model_version": "shadow-1",
        "active_oos_score": 0.0,
        "shadow_oos_score": 0.0,
        "last_validation_date": None,
        "last_promotion_date": None,
        "rollback_count": 0,
        "previous_weights": {},
        "score_at_promotion": 0.0,
    },
    "risk_guards": {
        "min_data_quality": 70.0,
        "min_liquidity_tl": 8000000.0,
        "max_overnight_risk": 65.0,
        "max_sector_like_concentration": 0.35,
        "max_candidates": 8,
        "min_signal_score": 72.0,
    },
    "audit_summary": {
        "last_audit_date": None,
        "total_signals_audited": 0,
        "win_rate_t3": 0.0,
        "win_rate_6m": 0.0,
        "six_month_outcomes": 0,
        "six_month_pending": 0,
        "six_month_status": "WARMUP",
        "six_month_horizon_trading_days": 126,
        "status": "BOOTSTRAP / LEARNING",
    },
    "data_quality": {},
}


def _merge(base: Dict, incoming: Dict) -> Dict:
    out = deepcopy(base)
    for k, v in (incoming or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _atomic_json_write(path: str, payload: Dict) -> bool:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        print(f"❌ State kayıt hatası: {exc}")
        return False


def load_ai_state() -> Dict:
    if not os.path.exists(STATE_FILE):
        state = deepcopy(DEFAULT_STATE)
        save_ai_state(state)
        return state
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        state = _merge(DEFAULT_STATE, raw)
        return state
    except Exception as exc:
        print(f"⚠️ State okuma hatası: {exc}. Güvenli varsayılan state kullanılacak.")
        return deepcopy(DEFAULT_STATE)


def save_ai_state(state: Dict) -> bool:
    state = _merge(DEFAULT_STATE, state or {})
    state["version"] = "5.0.0"
    return _atomic_json_write(STATE_FILE, state)


def save_model_weights(weights: Dict, status: str = "") -> bool:
    payload = {"weights": weights, "status": status, "version": "5.0.0"}
    return _atomic_json_write(WEIGHTS_FILE, payload)


def _lifecycle_columns():
    return [
        "tarih", "ticker", "entry_price", "initial_stop_price", "current_stop_price",
        "target_price", "last_seen_price", "meta_score", "pre_move_score", "flow_score",
        "resilience_score", "regime_fit_score", "quality_score", "risk_score", "data_quality",
        "market_regime", "regime_confidence", "model_version", "peak_price", "trough_price",
        "max_adverse_excursion", "max_favorable_excursion", "ret_t1", "ret_t3", "ret_t5",
        "ret_t10", "ret_t126", "outcome"
    ]


def load_lifecycle_signals() -> pd.DataFrame:
    cols = _lifecycle_columns()
    if not os.path.exists(LIFECYCLE_LOG_FILE):
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(LIFECYCLE_LOG_FILE)
        df["tarih"] = pd.to_datetime(df["tarih"], errors="coerce")
        # Backward-compatible migration: preserve existing rows and add new columns.
        for c in cols:
            if c not in df.columns:
                df[c] = pd.NA
        return df[cols]
    except Exception as exc:
        print(f"⚠️ Lifecycle okuma hatası: {exc}")
        return pd.DataFrame(columns=cols)


def save_lifecycle_signals(df: pd.DataFrame) -> None:
    out = df.copy()
    for c in _lifecycle_columns():
        if c not in out.columns:
            out[c] = pd.NA
    out[_lifecycle_columns()].to_csv(LIFECYCLE_LOG_FILE, index=False)


def load_signal_log() -> pd.DataFrame:
    """Load the signal ledger with backward-compatible schema migration."""
    required = [
        "tarih", "ticker", "entry_price", "meta_score", "pre_move_score", "flow_score",
        "resilience_score", "regime_fit_score", "quality_score", "risk_score",
        "ret_t1", "ret_t3", "ret_t5", "ret_t10"
    ]
    optional = [
        "ret_t126", "mfe_t10", "mae_t10", "outcome",
        "market_regime", "regime_confidence", "model_version", "data_quality"
    ]
    all_cols = required + optional

    if not os.path.exists(SIGNAL_LOG_FILE):
        return pd.DataFrame(columns=all_cols)

    try:
        df = pd.read_csv(SIGNAL_LOG_FILE)
        missing_required = [c for c in required if c not in df.columns]
        if missing_required:
            print(f"ℹ️ Signal log eksik zorunlu alanlar: {missing_required}; öğrenme atlandı.")
            return pd.DataFrame(columns=all_cols)

        df["tarih"] = pd.to_datetime(df["tarih"], errors="coerce").dt.normalize()
        numeric_cols = [
            "entry_price", "meta_score", "pre_move_score", "flow_score",
            "resilience_score", "regime_fit_score", "quality_score", "risk_score",
            "ret_t1", "ret_t3", "ret_t5", "ret_t10", "ret_t126",
            "mfe_t10", "mae_t10", "regime_confidence", "data_quality"
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        for col in optional:
            if col not in df.columns:
                df[col] = "PENDING" if col == "outcome" else np.nan

        return df[all_cols]
    except Exception as exc:
        print(f"⚠️ Signal log okuma hatası: {exc}")
        return pd.DataFrame(columns=all_cols)
