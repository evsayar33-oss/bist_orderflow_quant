"""Outcome resolution and controlled adaptive learning for Meta-Engine V1."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Tuple

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from state_manager import load_ai_state, load_signal_log, save_ai_state, save_model_weights

FACTOR_COLUMNS = ["pre_move_score", "flow_score", "resilience_score", "regime_fit_score", "quality_score"]
TARGET_COLUMNS = ["ret_t1", "ret_t3", "ret_t5", "ret_t10", "ret_t126"]
SIX_MONTH_TRADING_DAYS = 126


def _trading_day_index(history: pd.DataFrame) -> pd.DatetimeIndex:
    dates = pd.to_datetime(history["tarih"], errors="coerce").dropna().dt.normalize().sort_values().unique()
    return pd.DatetimeIndex(dates)


def resolve_forward_outcomes(signal_log: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    if signal_log is None or signal_log.empty or history is None or history.empty:
        return signal_log
    h = history.copy()
    h["tarih"] = pd.to_datetime(h["tarih"], errors="coerce").dt.normalize()
    h["close"] = pd.to_numeric(h["close"], errors="coerce")
    h["high"] = pd.to_numeric(h["high"], errors="coerce")
    h["low"] = pd.to_numeric(h["low"], errors="coerce")
    h = h.dropna(subset=["tarih", "ticker", "close"])
    dates = _trading_day_index(h)
    by_ticker = {t: g.sort_values("tarih") for t, g in h.groupby("ticker")}

    out = signal_log.copy()
    out["tarih"] = pd.to_datetime(out["tarih"], errors="coerce").dt.normalize()

    for idx, row in out.iterrows():
        ticker = row.get("ticker")
        sig_date = row.get("tarih")
        if ticker not in by_ticker or pd.isna(sig_date):
            continue
        g = by_ticker[ticker]
        future = g[g["tarih"] > sig_date].sort_values("tarih")
        if future.empty:
            continue
        entry = float(row.get("entry_price", row.get("close", np.nan)))
        if not np.isfinite(entry) or entry <= 0:
            continue
        for n in [1, 3, 5, 10]:
            if len(future) >= n and pd.isna(row.get(f"ret_t{n}")):
                px = float(future.iloc[n - 1]["close"])
                out.at[idx, f"ret_t{n}"] = round((px / entry - 1.0) * 100.0, 3)

        # 6-month forward outcome = 126 future trading sessions.
        # This is intentionally kept separate from trailing perf_6m features.
        if len(future) >= SIX_MONTH_TRADING_DAYS and pd.isna(row.get("ret_t126")):
            px_6m = float(future.iloc[SIX_MONTH_TRADING_DAYS - 1]["close"])
            out.at[idx, "ret_t126"] = round((px_6m / entry - 1.0) * 100.0, 3)

        window = future.iloc[: min(10, len(future))]
        if len(window):
            max_high = pd.to_numeric(window["high"], errors="coerce").max()
            min_low = pd.to_numeric(window["low"], errors="coerce").min()
            if np.isfinite(max_high):
                out.at[idx, "mfe_t10"] = round((max_high / entry - 1.0) * 100.0, 3)
            if np.isfinite(min_low):
                out.at[idx, "mae_t10"] = round((min_low / entry - 1.0) * 100.0, 3)

        if pd.notna(row.get("ret_t3")):
            out.at[idx, "outcome"] = "WIN_T3" if float(out.at[idx, "ret_t3"]) > 0 else "LOSS_T3"

    return out


def summarize_six_month_performance(signal_log: pd.DataFrame) -> Dict[str, float]:
    """
    Calculate a genuine forward 6-month win rate from resolved signal outcomes.

    Win definition: ret_t126 > 0 after 126 future trading sessions.
    Unresolved signals are excluded; they are not treated as losses or zeros.
    """
    if signal_log is None or signal_log.empty or "ret_t126" not in signal_log.columns:
        return {
            "win_rate_6m": 0.0,
            "six_month_outcomes": 0,
            "six_month_pending": 0,
            "six_month_status": "WARMUP",
            "six_month_horizon_trading_days": SIX_MONTH_TRADING_DAYS,
        }

    r = pd.to_numeric(signal_log["ret_t126"], errors="coerce")
    resolved = r.dropna()
    pending = int(r.isna().sum())
    n = int(len(resolved))
    if n == 0:
        status = "WARMUP"
        win_rate = 0.0
    else:
        status = "ACTIVE"
        win_rate = float((resolved > 0).mean() * 100.0)

    return {
        "win_rate_6m": round(win_rate, 1),
        "six_month_outcomes": n,
        "six_month_pending": pending,
        "six_month_status": status,
        "six_month_horizon_trading_days": SIX_MONTH_TRADING_DAYS,
    }


def _safe_corr(x, y):
    x = pd.to_numeric(x, errors="coerce")
    y = pd.to_numeric(y, errors="coerce")
    m = x.notna() & y.notna()
    if m.sum() < 8 or x[m].nunique() < 2 or y[m].nunique() < 2:
        return 0.0
    c, _ = spearmanr(x[m], y[m])
    return float(c) if np.isfinite(c) else 0.0


def _normalize_weights(raw: Dict[str, float], floor=0.05, ceiling=0.55) -> Dict[str, float]:
    cleaned = {k: max(0.0, float(v)) for k, v in raw.items()}
    total = sum(cleaned.values()) or 1.0
    w = {k: v / total for k, v in cleaned.items()}
    for k in w:
        w[k] = min(ceiling, max(floor, w[k]))
    total = sum(w.values()) or 1.0
    return {k: round(v / total, 4) for k, v in w.items()}


def propose_shadow_weights(signal_log: pd.DataFrame, state: Dict) -> Tuple[Dict[str, float], str]:
    valid_all = signal_log.dropna(subset=["ret_t3"]).copy() if signal_log is not None and not signal_log.empty else pd.DataFrame()
    min_samples = int(state.get("learning_params", {}).get("min_sample_size", 30))
    if len(valid_all) < min_samples:
        return state.get("shadow_weights", state.get("weights", {})), f"LEARNING_WAIT ({len(valid_all)}/{min_samples})"

    key_map = {
        "pre_move": "pre_move_score",
        "flow": "flow_score",
        "resilience": "resilience_score",
        "regime_fit": "regime_fit_score",
        "quality": "quality_score",
    }

    def propose_for_frame(frame: pd.DataFrame) -> Dict[str, float]:
        ic = {}
        y = pd.to_numeric(frame["ret_t3"], errors="coerce")
        for key, col in key_map.items():
            ic[key] = max(0.02, _safe_corr(frame[col], y))
        learned = _normalize_weights(ic, floor=0.05, ceiling=0.55)
        current = state.get("weights", {})
        lr = float(state.get("learning_params", {}).get("learning_rate", 0.08))
        max_shift = float(state.get("learning_params", {}).get("max_weight_shift", 0.08))
        shadow = {}
        for k, base in current.items():
            target = learned.get(k, base)
            proposed = float(base) * (1 - lr) + float(target) * lr
            proposed = np.clip(proposed, float(base) - max_shift, float(base) + max_shift)
            shadow[k] = proposed
        return _normalize_weights(shadow, floor=float(state["learning_params"]["min_weight"]), ceiling=float(state["learning_params"]["max_weight"]))

    shadow = propose_for_frame(valid_all)
    regime_models = {}
    if "market_regime" in valid_all.columns:
        for regime, frame in valid_all.groupby("market_regime"):
            if len(frame) >= max(12, min_samples // 2):
                regime_models[str(regime)] = propose_for_frame(frame)
    state["regime_weights"] = regime_models
    state["shadow_weights"] = shadow
    return shadow, f"SHADOW_PROPOSED ({len(valid_all)} samples; {len(regime_models)} regime models)"

def score_model_on_holdout(signal_log: pd.DataFrame, weights: Dict[str, float]) -> float:
    if signal_log is None or signal_log.empty:
        return 0.0
    valid = signal_log.dropna(subset=["ret_t3"]).copy()
    if len(valid) < 10:
        return 0.0
    y = pd.to_numeric(valid["ret_t3"], errors="coerce").fillna(0.0)
    score = (
        pd.to_numeric(valid["pre_move_score"], errors="coerce").fillna(50) * weights.get("pre_move", 0.4)
        + pd.to_numeric(valid["flow_score"], errors="coerce").fillna(50) * weights.get("flow", 0.25)
        + pd.to_numeric(valid["resilience_score"], errors="coerce").fillna(50) * weights.get("resilience", 0.15)
        + pd.to_numeric(valid["regime_fit_score"], errors="coerce").fillna(50) * weights.get("regime_fit", 0.1)
        + pd.to_numeric(valid["quality_score"], errors="coerce").fillna(50) * weights.get("quality", 0.1)
    )
    corr = _safe_corr(score, y)
    win = float((y > 0).mean())
    avg = float(y.mean())
    # A stable composite, not a raw return maximizer.
    return round(corr * 0.55 + win * 0.25 + np.tanh(avg / 5.0) * 0.20, 6)


def _temporal_split(history: pd.DataFrame):
    if history is None or history.empty or "tarih" not in history.columns:
        return pd.DataFrame(), pd.DataFrame()
    h = history.copy()
    h["tarih"] = pd.to_datetime(h["tarih"], errors="coerce").dt.normalize()
    h = h.dropna(subset=["tarih"]).sort_values("tarih")
    dates = h["tarih"].drop_duplicates().tolist()
    if len(dates) < 5:
        cut = max(1, int(len(h) * 0.8))
        return h.iloc[:cut].copy(), h.iloc[cut:].copy()
    cut_date_idx = max(1, int(len(dates) * 0.80))
    cut_date = dates[cut_date_idx - 1]
    return h[h["tarih"] <= cut_date].copy(), h[h["tarih"] > cut_date].copy()


def apply_learning(history: pd.DataFrame, state: Dict) -> Dict:
    train, holdout = _temporal_split(history.dropna(subset=["ret_t3"]) if history is not None and not history.empty else pd.DataFrame())
    min_samples = int(state["learning_params"].get("shadow_min_sample", 20))
    if len(train) < min_samples or len(holdout) < max(8, min_samples // 3):
        state["audit_summary"]["status"] = f"VALIDATION_WAIT (train={len(train)}, holdout={len(holdout)})"
        state["audit_summary"]["total_signals_audited"] = int(len(train) + len(holdout))
        save_ai_state(state)
        return state

    shadow, status = propose_shadow_weights(train, state)
    state["shadow_weights"] = shadow
    save_model_weights(shadow, status)

    active_score = score_model_on_holdout(holdout, state.get("weights", {}))
    shadow_score = score_model_on_holdout(holdout, shadow)
    validation = state["validation"]
    validation["active_oos_score"] = active_score
    validation["shadow_oos_score"] = shadow_score
    validation["last_validation_date"] = datetime.now().strftime("%Y-%m-%d")
    validation["holdout_samples"] = int(len(holdout))
    validation["train_samples"] = int(len(train))

    margin = float(state["learning_params"].get("promotion_margin", 0.05))
    promoted_score = float(validation.get("score_at_promotion", 0.0))
    previous = validation.get("previous_weights", {})

    # Roll back only after temporal holdout evidence shows material degradation.
    if previous and promoted_score > 0 and active_score < promoted_score - 0.12:
        state["weights"] = previous
        validation["active_model_version"] = f"rollback-{datetime.now().strftime('%Y%m%d%H%M')}"
        validation["rollback_count"] = int(validation.get("rollback_count", 0)) + 1
        validation["previous_weights"] = {}
        validation["score_at_promotion"] = score_model_on_holdout(holdout, state["weights"])
        state["audit_summary"]["status"] = "ROLLBACK_TRIGGERED"
    elif shadow_score > active_score + margin:
        validation["previous_weights"] = dict(state.get("weights", {}))
        state["weights"] = shadow
        validation["active_model_version"] = f"champion-{datetime.now().strftime('%Y%m%d%H%M')}"
        validation["shadow_model_version"] = f"shadow-{datetime.now().strftime('%Y%m%d%H%M')}"
        validation["last_promotion_date"] = datetime.now().strftime("%Y-%m-%d")
        validation["score_at_promotion"] = shadow_score
        state["audit_summary"]["status"] = f"PROMOTED_OOS (train={len(train)}, holdout={len(holdout)})"
    else:
        state["audit_summary"]["status"] = status + " | OOS_REJECTED_OR_UNCHANGED"

    y = pd.to_numeric(history["ret_t3"], errors="coerce") if history is not None and not history.empty else pd.Series(dtype=float)
    if not y.empty:
        state["audit_summary"]["total_signals_audited"] = int(y.notna().sum())
        state["audit_summary"]["win_rate_t3"] = round(float((y.dropna() > 0).mean()) * 100.0, 1)
    state["audit_summary"]["last_audit_date"] = datetime.now().strftime("%Y-%m-%d")
    # Keep the 6-month forward metric independent from the short-horizon learner.
    state["audit_summary"].update(summarize_six_month_performance(history))
    save_ai_state(state)
    return state
