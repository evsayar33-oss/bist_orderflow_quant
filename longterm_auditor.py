import pandas as pd
import numpy as np
import requests
import os
from datetime import datetime
from scipy.stats import spearmanr
from state_manager import load_ai_state, save_ai_state, load_lifecycle_signals, LIFECYCLE_LOG_FILE

def fetch_current_market_snapshot():
    """Tüm hisselerin güncel fiyat ve günlük dip/tepe verisini TradingView'den çeker."""
    url = "https://scanner.tradingview.com/turkey/scan"
    payload = {
        "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
        "columns": ["name", "close", "high", "low"],
        "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"},
        "range": [0, 500]
    }
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json().get("data", [])
            price_map = {}
            for item in data:
                d = item["d"]
                ticker = d[0]
                close_p = float(d[1]) if d[1] is not None else 0.0
                high_p = float(d[2]) if d[2] is not None else close_p
                low_p = float(d[3]) if d[3] is not None else close_p
                price_map[ticker] = {"close": close_p, "high": high_p, "low": low_p}
            return price_map
    except Exception as e:
        print(f"⚠️ Denetçi piyasa verisi çekemedi: {e}")
    return {}

def update_signal_lifecycle(df_signals, market_prices, state):
    """Sinyallerin T+15, T+30, T+60 getirilerini ve Max Drawdown riskini günceller."""
    if df_signals.empty or not market_prices:
        return df_signals

    today = pd.Timestamp.now().normalize()
    thresholds = state["thresholds"]
    stop_loss = thresholds.get("stop_loss_pct", -10.0)
    target_30d = thresholds.get("target_swing_30d_pct", 15.0)
    target_60d = thresholds.get("target_long_60d_pct", 30.0)

    for idx, row in df_signals.iterrows():
        ticker = row["ticker"]
        if ticker not in market_prices:
            continue

        entry_p = float(row["entry_price"])
        if entry_p <= 0:
            continue

        curr_p = market_prices[ticker]["close"]
        curr_low = market_prices[ticker]["low"]
        curr_high = market_prices[ticker]["high"]
        
        # Güncel getiri ve anlık ekstremumlar
        gain_from_entry = ((curr_p - entry_p) / entry_p) * 100.0
        low_from_entry = ((curr_low - entry_p) / entry_p) * 100.0
        high_from_entry = ((curr_high - entry_p) / entry_p) * 100.0

        # Max Drawdown & Peak Gain takibi
        prev_drawdown = float(row.get("max_drawdown", 0.0)) if pd.notna(row.get("max_drawdown")) else 0.0
        df_signals.at[idx, "max_drawdown"] = round(min(prev_drawdown, low_from_entry), 2)

        prev_peak = float(row.get("peak_gain", 0.0)) if pd.notna(row.get("peak_gain")) else 0.0
        df_signals.at[idx, "peak_gain"] = round(max(prev_peak, high_from_entry), 2)

        # Gün farkı
        sig_date = pd.to_datetime(row["tarih"])
        days_passed = (today - sig_date).days

        # Vade getirilerinin kaydı
        if days_passed >= 15 and pd.isna(row.get("ret_15d")):
            df_signals.at[idx, "ret_15d"] = round(gain_from_entry, 2)
        if days_passed >= 30 and pd.isna(row.get("ret_30d")):
            df_signals.at[idx, "ret_30d"] = round(gain_from_entry, 2)
        if days_passed >= 60 and pd.isna(row.get("ret_60d")):
            df_signals.at[idx, "ret_60d"] = round(gain_from_entry, 2)

        # Yaşam Döngüsü Durumu (Outcome) Belirleme
        cur_dd = df_signals.at[idx, "max_drawdown"]
        
        if cur_dd <= stop_loss:
            df_signals.at[idx, "outcome"] = "FAIL_FALSE_BOTTOM"
        elif days_passed >= 60 and gain_from_entry >= target_60d:
            df_signals.at[idx, "outcome"] = "WIN_MULTI_BAGGER"
        elif days_passed >= 30 and gain_from_entry >= target_30d:
            df_signals.at[idx, "outcome"] = "WIN_SWING"
        elif days_passed >= 60:
            df_signals.at[idx, "outcome"] = "CONSOLIDATING"
        else:
            if row.get("outcome") != "FAIL_FALSE_BOTTOM":
                df_signals.at[idx, "outcome"] = "PENDING"

    df_signals.to_csv(LIFECYCLE_LOG_FILE, index=False)
    return df_signals

def run_feedback_loop_optimization(df_signals, state):
    """
    Geçmiş sinyalleri denetler; faktörlerin gelecekteki getiriyle korelasyonunu (IC) hesaplayıp
    ağırlıkları ve eşik değerleri otonom optimize eder.
    """
    mature_signals = df_signals[df_signals["outcome"].isin(["WIN_MULTI_BAGGER", "WIN_SWING", "FAIL_FALSE_BOTTOM", "CONSOLIDATING"])]
    min_samples = state["learning_params"].get("min_sample_size", 12)

    if len(mature_signals) < min_samples:
        status_msg = f"🕒 ÖĞRENME EVRESİNDE (Olgunlaşan Sinyal: {len(mature_signals)}/{min_samples})"
        state["audit_summary"]["status"] = status_msg
        state["audit_summary"]["total_signals_audited"] = len(mature_signals)
        state["audit_summary"]["last_audit_date"] = datetime.now().strftime("%Y-%m-%d")
        save_ai_state(state)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {status_msg}")
        return state

    # 1. Başarı & Risk Metrikleri
    wins = mature_signals[mature_signals["outcome"].str.startswith("WIN")]
    fails = mature_signals[mature_signals["outcome"] == "FAIL_FALSE_BOTTOM"]
    
    win_rate = round((len(wins) / len(mature_signals)) * 100.0, 1)
    avg_dd = round(float(mature_signals["max_drawdown"].mean()), 1)

    # 2. Faktör Bilgi Katsayısı (IC) & Korelasyon Analizi
    factors = {
        "accum": "score_accum",
        "fundamental": "score_fund",
        "sweep": "score_sweep",
        "vol_mom": "score_vol"
    }
    
    # Getiri hedefi olarak 30 günlük getiri veya tepe getiri kullanılır
    returns_target = mature_signals["ret_30d"].fillna(mature_signals["peak_gain"]).values
    ic_scores = {}

    for f_key, col in factors.items():
        x = pd.to_numeric(mature_signals[col], errors='coerce').fillna(50.0).values
        if np.std(x) > 0 and np.std(returns_target) > 0:
            corr, _ = spearmanr(x, returns_target)
            ic = corr if not np.isnan(corr) else 0.05
        else:
            ic = 0.05
        # Negatif korelasyon gösteren faktöre taban puan ver (tamamen sıfırlama)
        ic_scores[f_key] = max(ic, 0.05)

    # 3. Hata Analizi: Sahte Diplere (FAIL_FALSE_BOTTOM) yol açan faktörü cezalandır
    penalized_factor = None
    rewarded_factor = max(ic_scores, key=ic_scores.get)

    if len(fails) >= 3:
        # Hatalı sinyallerde en yüksek olan ama başarı getirmeyen faktörü tespit et
        fail_factor_means = {f_key: mature_signals.loc[fails.index, col].mean() for f_key, col in factors.items()}
        penalized_factor = max(fail_factor_means, key=fail_factor_means.get)
        ic_scores[penalized_factor] = max(ic_scores[penalized_factor] * 0.70, 0.05)

    # 4. Bayesian Öğrenme ile Ağırlık Güncelleme (Shrinkage)
    total_ic = sum(ic_scores.values())
    raw_weights = {k: ic_scores[k] / total_ic for k in ic_scores}

    lr = state["learning_params"]["learning_rate"]
    min_w = state["learning_params"]["min_weight"]
    max_w = state["learning_params"]["max_weight"]
    current_weights = state["weights"]

    updated_weights = {}
    for k in current_weights:
        new_w = (1.0 - lr) * current_weights[k] + lr * raw_weights[k]
        clamped_w = min(max(new_w, min_w), max_w)
        updated_weights[k] = clamped_w

    # Toplamı tam 1.0 yap
    w_sum = sum(updated_weights.values())
    final_weights = {k: round(v / w_sum, 3) for k, v in updated_weights.items()}

    # 5. State Güncelleme
    state["weights"] = final_weights
    state["audit_summary"]["total_signals_audited"] = len(mature_signals)
    state["audit_summary"]["win_rate_30d"] = win_rate
    state["audit_summary"]["avg_max_drawdown"] = avg_dd
    state["audit_summary"]["last_penalized_factor"] = penalized_factor
    state["audit_summary"]["last_rewarded_factor"] = rewarded_factor
    state["audit_summary"]["last_audit_date"] = datetime.now().strftime("%Y-%m-%d")
    state["audit_summary"]["status"] = f"🧠 OTONOM AI AKTİF (Örneklem: {len(mature_signals)} | WinRate: %{win_rate})"

    save_ai_state(state)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Denetim Tamamlandı. Yeni Ağırlıklar: {final_weights}")
    return state

def audit_and_calibrate():
    """Dışarıdan ve crondan doğrudan çağrılan ana denetçi fonksiyonu."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔍 Orta-Uzun Vade Akümülasyon Denetçisi Çalışıyor...")
    state = load_ai_state()
    df_signals = load_lifecycle_signals()
    
    if df_signals.empty:
        print("ℹ️ Henüz incelenecek sinyal kaydı yok.")
        return state

    market_prices = fetch_current_market_snapshot()
    if not market_prices:
        print("⚠️ Güncel fiyatlar çekilemediği için denetim atlandı.")
        return state

    df_updated = update_signal_lifecycle(df_signals, market_prices, state)
    state = run_feedback_loop_optimization(df_updated, state)
    return state

if __name__ == "__main__":
    audit_and_calibrate()
