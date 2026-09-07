import json
import os
import pandas as pd
from datetime import datetime

STATE_FILE = "longterm_ai_state.json"
LIFECYCLE_LOG_FILE = "signals_lifecycle.csv"

DEFAULT_STATE = {
    "version": "1.0.0",
    "strategy": "BIST_SWING_ACCUMULATION",
    "weights": {
        "accum": 0.35,
        "fundamental": 0.25,
        "sweep": 0.25,
        "vol_mom": 0.15
    },
    "thresholds": {
        "min_quant_score": 65.0,
        "max_falling_knife_drop_1m": -18.0,
        "min_dist_from_bottom": 2.0,
        "max_dist_from_bottom": 13.0,
        "stop_loss_pct": -10.0,
        "target_swing_30d_pct": 15.0,
        "target_long_60d_pct": 30.0
    },
    "learning_params": {
        "learning_rate": 0.05,
        "min_weight": 0.08,
        "max_weight": 0.55,
        "min_sample_size": 12
    },
    "audit_summary": {
        "last_audit_date": datetime.now().strftime("%Y-%m-%d"),
        "total_signals_audited": 0,
        "win_rate_30d": 0.0,
        "win_rate_60d": 0.0,
        "avg_max_drawdown": 0.0,
        "last_penalized_factor": None,
        "last_rewarded_factor": None,
        "status": "🕒 ÖĞRENME EVRESİNDE (Yetersiz Örneklem - Baz Ağırlıklar Aktif)"
    }
}

def load_ai_state():
    """State dosyasını okur. Dosya yoksa veya bozuksa güvenli default döner."""
    if not os.path.exists(STATE_FILE):
        save_ai_state(DEFAULT_STATE)
        return DEFAULT_STATE
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
            # Kritik anahtar kontrolleri
            for k in ["weights", "thresholds", "learning_params", "audit_summary"]:
                if k not in state:
                    state[k] = DEFAULT_STATE[k]
            return state
    except Exception as e:
        print(f"⚠️ State okuma hatası ({e}), default state yükleniyor.")
        return DEFAULT_STATE

def save_ai_state(state):
    """State dosyasını atomic olarak diske yazar."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"❌ State kayıt hatası: {e}")
        return False

def load_lifecycle_signals():
    """Sinyal yaşam döngüsü veri setini yükler."""
    if not os.path.exists(LIFECYCLE_LOG_FILE):
        cols = [
            "tarih", "ticker", "entry_price", "quant_score", "regime",
            "score_accum", "score_fund", "score_sweep", "score_vol",
            "ret_15d", "ret_30d", "ret_60d", "max_drawdown", "peak_gain", "outcome"
        ]
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(LIFECYCLE_LOG_FILE)
        df["tarih"] = pd.to_datetime(df["tarih"])
        return df
    except Exception as e:
        print(f"⚠️ Sinyal defteri okuma hatası: {e}")
        return pd.DataFrame()
