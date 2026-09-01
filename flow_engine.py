import numpy as np
import pandas as pd
import os
import warnings

warnings.filterwarnings('ignore')

GECMIS_DOSYA = "gecmis_veri.csv"

def gecmis_veriyi_yukle():
    if os.path.exists(GECMIS_DOSYA):
        try:
            df = pd.read_csv(GECMIS_DOSYA)
            if 'tarih' in df.columns:
                df['tarih'] = pd.to_datetime(df['tarih'])
            return df
        except: 
            return pd.DataFrame()
    return pd.DataFrame()

def calculate_quant_scores(df, df_gecmis, dynamic_weights=None):
    if df.empty: 
        return df

    scored_data = []

    for idx, row in df.iterrows():
        item = row.to_dict()
        
        close = float(item.get('close', 0.0))
        high = float(item.get('high', close))
        low = float(item.get('low', close))
        change = float(item.get('change_%', 0.0))
        rvol = float(item.get('rvol', 1.0))
        f_ratio = float(item.get('foreign_ratio', 20.0))
        
        donch_upper = float(item.get('donch_upper', 0.0))
        donch_lower = float(item.get('donch_lower', 0.0))
        roe = float(item.get('roe', 15.0))
        pb = float(item.get('pb', 2.0))

        # =========================================================================
        # 1. DOĞRU PİVOT KIRILIM UZAKLIĞI (20 GÜNLÜK ZİRVEDEN SAPMA %)
        # =========================================================================
        # Fiyatın 20 Günlük Kırılım Seviyesine (Pivot) Uzaklığı:
        # %0 ile %5 arası = TAM BUGÜN KIRAN TAZE HİSSE!
        pivot_distance = ((close - donch_upper) / (donch_upper + 1e-9)) * 100.0 if donch_upper > 0 else 0.0
        
        # 20 Günlük Taban Sıkışma Genişliği (Dar Taban = Güçlü Yay)
        base_width = ((donch_upper - donch_lower) / (close + 1e-9)) * 100.0 if close > 0 else 30.0

        # --- KIRILIM TAZELİK PUANI ---
        freshness_score = 0.0
        if -3.0 <= pivot_distance <= 6.0:  # Tam 20 günün zirvesinde veya yeni kırıyor!
            freshness_score += 65.0
            if base_width <= 25.0:         # Dar sıkışma tabanı bonusu
                freshness_score += 35.0
            elif base_width <= 35.0:
                freshness_score += 20.0
        elif 6.0 < pivot_distance <= 12.0: # Kırılımın 2. günü
            freshness_score += 45.0
        elif pivot_distance > 15.0:        # Zaten %15+ primlenmişse tren kaçmıştır
            freshness_score = 5.0
        else:
            freshness_score = 15.0

        # =========================================================================
        # 2. KURUMSAL SÜPÜRME VE HACİM
        # =========================================================================
        range_span = high - low
        clv = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        sweep_ratio = (f_ratio * 0.40) + (max(clv, 0) * 60.0)
        sweep_ratio = round(min(max(sweep_ratio, 5.0), 98.5), 1)

        vol_z = float((rvol - 1.0) * 1.85)
        vol_z = round(min(max(vol_z, -2.0), 5.0), 2)

        quality_score = 50.0
        if roe >= 15.0: quality_score += 30.0
        if pb <= 5.0: quality_score += 20.0

        item['pivot_distance'] = round(pivot_distance, 1)
        item['base_width'] = round(base_width, 1)
        item['freshness_score'] = freshness_score
        item['sweep_ratio'] = sweep_ratio
        item['vol_z'] = vol_z
        item['quality_score'] = quality_score
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty: 
        return res_df

    # Yüzdelik Normalizasyon
    res_df['pct_fresh'] = res_df['freshness_score'].rank(pct=True) * 100.0
    res_df['pct_sweep'] = res_df['sweep_ratio'].rank(pct=True) * 100.0
    res_df['pct_vol'] = res_df['vol_z'].rank(pct=True) * 100.0
    res_df['pct_qual'] = res_df['quality_score'].rank(pct=True) * 100.0

    # NİHAİ SKOR: %45 Kırılım Tazeliği + %25 Süpürme + %20 Hacim + %10 Kalite
    raw_score = np.round(
        res_df['pct_fresh'] * 0.45 + 
        res_df['pct_sweep'] * 0.25 + 
        res_df['pct_vol'] * 0.20 + 
        res_df['pct_qual'] * 0.10, 
        1
    )
    
    # Sadece o gün pozitif kapatan ve pivottan %12'den fazla uzaklaşmamış olanlar tam puan alır
    res_df['quant_score'] = np.where(
        (res_df['change_%'] > 0.0) & (res_df['pivot_distance'] <= 12.0),
        raw_score,
        0.0 # Treni kaçanları ve negatifleri sıfırla
    )

    # Rejim Tespiti
    conditions = [
        (res_df['pivot_distance'] > 12.0),
        (res_df['quant_score'] >= 70.0) & (res_df['freshness_score'] >= 60.0),
        (res_df['quant_score'] >= 50.0),
        (res_df['change_%'] < -1.5)
    ]
    choices = [
        "🚫 TREN KAÇTI (PİVOTTAN AŞIRI UZAK)",
        "🚀 TAZE TABAN KIRILIMI (DAY 1-2 PİVOT)",
        "⚡ KIRILIM ADAYI (SIKIŞMA)",
        "🚨 KURUMSAL BOŞALTIM (DUMP)"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR")

    drop_cols = ['pct_fresh', 'pct_sweep', 'pct_vol', 'pct_qual', 'quality_score', 'freshness_score']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    # Düne Göre Skor Farkı
    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)
