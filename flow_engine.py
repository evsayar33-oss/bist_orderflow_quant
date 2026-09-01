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
        
        high_1m = float(item.get('high_1m', close))
        low_1m = float(item.get('low_1m', close))
        roe = float(item.get('roe', 15.0))
        pb = float(item.get('pb', 2.0))

        # 1. TABAN KONUMU VE DİP MESAFESİ HESABI
        channel_span = high_1m - low_1m
        if channel_span > 0:
            range_position = ((close - low_1m) / channel_span) * 100.0
        else:
            range_position = 50.0
            
        dist_from_support = ((close - low_1m) / (low_1m + 1e-9)) * 100.0 if low_1m > 0 else 0.0

        # Dip Akümülasyon Puanı (Kanalın alt %10-%45 tabanında olanlara tam puan)
        accumulation_score = 0.0
        if 5.0 <= range_position <= 45.0 and dist_from_support <= 10.0:
            accumulation_score = 80.0
        elif range_position < 60.0 and dist_from_support <= 15.0:
            accumulation_score = 60.0
        elif range_position >= 85.0:
            accumulation_score = 10.0
        else:
            accumulation_score = 30.0

        # 2. DİPTE KURUMSAL SÜPÜRME VE HACİM
        range_span = high - low
        clv = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        sweep_ratio = (f_ratio * 0.40) + (max(clv, 0) * 60.0)
        sweep_ratio = round(min(max(sweep_ratio, 5.0), 98.5), 1)

        vol_z = float((rvol - 1.0) * 1.85)
        vol_z = round(min(max(vol_z, -2.0), 5.0), 2)

        quality_score = 50.0
        if roe >= 15.0: quality_score += 30.0
        if pb <= 5.0: quality_score += 20.0

        item['range_position'] = round(range_position, 1)
        item['dist_from_support'] = round(dist_from_support, 1)
        item['accumulation_score'] = accumulation_score
        item['sweep_ratio'] = sweep_ratio
        item['vol_z'] = vol_z
        item['quality_score'] = quality_score
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty: 
        return res_df

    # Yüzdelik Normalizasyon
    res_df['pct_accum'] = res_df['accumulation_score'].rank(pct=True) * 100.0
    res_df['pct_sweep'] = res_df['sweep_ratio'].rank(pct=True) * 100.0
    res_df['pct_vol'] = res_df['vol_z'].rank(pct=True) * 100.0
    res_df['pct_qual'] = res_df['quality_score'].rank(pct=True) * 100.0

    raw_score = np.round(
        res_df['pct_accum'] * 0.45 + 
        res_df['pct_sweep'] * 0.25 + 
        res_df['pct_vol'] * 0.20 + 
        res_df['pct_qual'] * 0.10, 
        1
    )
    
    # Sadece o gün pozitif kapatanlar tam puan alır
    res_df['quant_score'] = np.where(
        res_df['change_%'] > 0.0,
        raw_score,
        0.0
    )

    # Rejim Tespiti
    conditions = [
        (res_df['range_position'] >= 85.0),
        (res_df['quant_score'] >= 70.0) & (res_df['range_position'] <= 50.0),
        (res_df['quant_score'] >= 50.0),
        (res_df['change_%'] < -1.5)
    ]
    choices = [
        "🚫 ZİRVEDE (RİSKLİ BÖLGE)",
        "🎯 DİP AKÜMÜLASYONU (TABANDAN DÖNÜŞ)",
        "⚡ TABANDA SIKIŞMA (ADAY)",
        "🚨 KURUMSAL BOŞALTIM (DUMP)"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR")

    drop_cols = ['pct_accum', 'pct_sweep', 'pct_vol', 'pct_qual', 'quality_score', 'accumulation_score']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    # Düne Göre Fark
    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)
