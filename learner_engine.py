import pandas as pd
import numpy as np
import os
from scipy.stats import spearmanr

SIGNAL_LOG_FILE = "signals_log.csv"
WEIGHTS_FILE = "model_weights.json"

# Temel Başlangıç Ağırlıkları (Cold-Start Priors)
DEFAULT_WEIGHTS = {
    "persistence": 0.40,
    "growth": 0.25,
    "sweep": 0.25,
    "vol_z": 0.10
}

def load_signal_history():
    if os.path.exists(SIGNAL_LOG_FILE):
        try:
            df = pd.read_csv(SIGNAL_LOG_FILE)
            df['tarih'] = pd.to_datetime(df['tarih'])
            return df
        except:
            return pd.DataFrame()
    return pd.DataFrame()

def log_today_signals(top_df):
    """Günün en iyi sinyallerini gelecekte getirisini ölçmek üzere kaydeder."""
    if top_df.empty:
        return
    
    signals = top_df.head(10)[['ticker', 'close', 'quant_score', 'pct_persistence', 'pct_growth', 'pct_sweep', 'pct_vol_z', 'tarih']].copy()
    signals['realized_return_3d'] = np.nan
    signals['realized_return_5d'] = np.nan
    
    history_df = load_signal_history()
    if not history_df.empty:
        # Aynı günün kaydını güncelle
        today_val = pd.Timestamp.now().normalize()
        history_df = history_df[history_df['tarih'] != today_val]
        updated_history = pd.concat([history_df, signals], ignore_index=True)
    else:
        updated_history = signals
        
    updated_history.to_csv(SIGNAL_LOG_FILE, index=False)

def update_realized_returns(current_market_df):
    """Geçmişte üretilen sinyallerin T+3 ve T+5 gün sonraki gerçek kâr/zararını ölçer."""
    history_df = load_signal_history()
    if history_df.empty or current_market_df.empty:
        return history_df
    
    price_map = dict(zip(current_market_df['ticker'], current_market_df['close']))
    today = pd.Timestamp.now().normalize()
    
    for idx, row in history_df.iterrows():
        sig_date = pd.to_datetime(row['tarih'])
        days_passed = (today - sig_date).days
        ticker = row['ticker']
        entry_price = float(row['close'])
        
        if ticker in price_map and entry_price > 0:
            current_price = price_map[ticker]
            gain = ((current_price - entry_price) / entry_price) * 100.0
            
            # T+3 ve T+5 getirilerini doldur
            if days_passed >= 3 and pd.isna(row['realized_return_3d']):
                history_df.at[idx, 'realized_return_3d'] = round(gain, 2)
            if days_passed >= 5 and pd.isna(row['realized_return_5d']):
                history_df.at[idx, 'realized_return_5d'] = round(gain, 2)
                
    history_df.to_csv(SIGNAL_LOG_FILE, index=False)
    return history_df

def calibrate_dynamic_weights():
    """
    WALK-FORWARD MACHINE LEARNING KALİBRASYONU:
    Geçmiş sinyallerin başarısını analiz eder ve faktörlerin ağırlıklarını optimize eder.
    """
    history_df = load_signal_history()
    
    # Yeterli geri besleme verisi yoksa (İlk 1-2 hafta) temel ağırlıkları koru
    valid_samples = history_df.dropna(subset=['realized_return_3d']) if not history_df.empty else pd.DataFrame()
    if len(valid_samples) < 15:
        return DEFAULT_WEIGHTS, "🕒 ÖĞRENME EVRESİNDE (Yetersiz Örneklem - Baz Ağırlıklar Aktif)"
    
    # Faktörlerin gelecekteki getiriyle olan Bilgi Katsayısını (Information Coefficient - IC) hesapla
    factors = ['pct_persistence', 'pct_growth', 'pct_sweep', 'pct_vol_z']
    ic_scores = {}
    
    y = valid_samples['realized_return_3d'].values
    for f in factors:
        x = valid_samples[f].values
        if np.std(x) > 0 and np.std(y) > 0:
            corr, _ = spearmanr(x, y)
            # Negatif korelasyonu taban 0.05'e çek, pozitifleri ödüllendir
            ic_scores[f] = max(corr if not np.isnan(corr) else 0.05, 0.05)
        else:
            ic_scores[f] = 0.05
            
    # IC Skorlarına göre normalize ağırlık üret
    total_ic = sum(ic_scores.values())
    learned_weights = {
        "persistence": round(ic_scores['pct_persistence'] / total_ic, 2),
        "growth": round(ic_scores['pct_growth'] / total_ic, 2),
        "sweep": round(ic_scores['pct_sweep'] / total_ic, 2),
        "vol_z": round(ic_scores['pct_vol_z'] / total_ic, 2)
    }
    
    # Aşırı sapmaları engellemek için %60 Baz Ağırlık + %40 Öğrenilen Ağırlık (Bayesian Shrinkage)
    smoothed_weights = {
        k: round(0.50 * DEFAULT_WEIGHTS[k] + 0.50 * learned_weights[k], 2)
        for k in DEFAULT_WEIGHTS
    }
    
    # Toplamı 1.0 yap
    w_sum = sum(smoothed_weights.values())
    final_weights = {k: round(v / w_sum, 2) for k, v in smoothed_weights.items()}
    
    status_msg = f"🧠 AI DİNAMİK KALİBRASYON AKTİF (Örneklem: {len(valid_samples)})"
    return final_weights, status_msg
