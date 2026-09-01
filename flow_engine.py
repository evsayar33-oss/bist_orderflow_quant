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
        perf_w = float(item.get('perf_w', 0.0))
        perf_1m = float(item.get('perf_1m', 0.0))
        roe = float(item.get('roe', 15.0))
        pb = float(item.get('pb', 2.0))

        # =========================================================================
        # 1. TAZE PİVOT KIRILIMI & TABANDAN UZAKLIK HESABI (DAY 1-2 BREAKOUT)
        # =========================================================================
        # 20 Günlük Zirveye Yakınlık Oranı (1.0 = Tam Zirvede/Kırıyor)
        pivot_proximity = (close / donch_upper) if donch_upper > 0 else 0.8
        
        # 20 Günlük Tabandan Uzaklık (Ne kadar azsa patlama o kadar tazedir!)
        base_range = donch_upper - donch_lower
        distance_from_base = ((close - donch_lower) / (donch_lower + 1e-9)) * 100.0 if donch_lower > 0 else 50.0
        
        # 20 Günlük Taban Sıkışma Genişliği (Dar bant = Büyük Yaylanma)
        base_tightness = (base_range / (close + 1e-9)) if close > 0 else 0.5

        # --- KIRILIM TAZELİK PUANI (0 - 100) ---
        freshness_score = 0.0
        if pivot_proximity >= 0.98:  # 20 Günün Zirvesini Tam Bugün Kırıyor!
            freshness_score += 50.0
            if distance_from_base <= 12.0:  # Tabandan sadece %0-%12 uzakta (Yeni Başlıyor!)
                freshness_score += 50.0
            elif distance_from_base <= 20.0:
                freshness_score += 30.0
            else:
                freshness_score += 10.0  # Zaten çok primlenmişse az puan ver
        elif pivot_proximity >= 0.94:
            freshness_score += 30.0
            if distance_from_base <= 10.0:
                freshness_score += 30.0

        # =========================================================================
        # 2. HACİMLİ ATEŞLEME & EMİR AKIŞI (IGNITION FLOW)
        # =========================================================================
        range_span = high - low
        clv = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        sweep_ratio = (f_ratio * 0.40) + (max(clv, 0) * 60.0)
        sweep_ratio = round(min(max(sweep_ratio, 5.0), 98.5), 1)

        vol_z = float((rvol - 1.0) * 1.85)
        vol_z = round(min(max(vol_z, -2.0), 5.0), 2)

        # Temel Sağlık Çarpanı (ROE pozitif olanlara destek)
        quality_score = 50.0
        if roe >= 20.0: quality_score += 30.0
        elif roe > 0.0: quality_score += 15.0
        if pb <= 5.0: quality_score += 20.0

        item['pivot_proximity'] = round(pivot_proximity * 100.0, 1)
        item['distance_from_base'] = round(distance_from_base, 1)
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

    # NİHAİ TAZE KIRILIM SKORU:
    # %40 Kırılım Tazeliği & Taban Yakınlığı + %25 Süpürme + %20 Hacim Şoku + %15 Temel Kalite
    raw_score = np.round(
        res_df['pct_fresh'] * 0.40 + 
        res_df['pct_sweep'] * 0.25 + 
        res_df['pct_vol'] * 0.20 + 
        res_df['pct_qual'] * 0.15, 
        1
    )
    
    # Sadece o gün pozitif kapatanları ve tabandan %25'ten fazla uzaklaşmamışları ödüllendir
    res_df['quant_score'] = np.where(
        (res_df['change_%'] > 0.0) & (res_df['distance_from_base'] <= 35.0),
        raw_score,
        np.round(raw_score * 0.10, 1) # Treni kaçmış olanları tabana at
    )

    # Rejim Sınıflandırması
    conditions = [
        (res_df['distance_from_base'] > 35.0),
        (res_df['quant_score'] >= 75.0) & (res_df['freshness_score'] >= 70.0),
        (res_df['quant_score'] >= 60.0),
        (res_df['change_%'] < -1.5)
    ]
    choices = [
        "🚫 TREN KAÇTI (TABANDAN AŞIRI UZAK)",
        "🚀 TAZE TABAN KIRILIMI (DAY 1-2 PİVOT)",
        "⚡ KIRILIM ADAYI (SIKIŞMA BÖLGESİ)",
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
