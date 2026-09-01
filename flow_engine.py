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
        perf_w = float(item.get('perf_w', 0.0))
        perf_1m = float(item.get('perf_1m', 0.0))
        roe = float(item.get('roe', 15.0))
        pb = float(item.get('pb', 2.0))

        # =========================================================================
        # 1. 20 GÜNLÜK PİVOT KIRILIM NOKTASI (DAY-1 / DAY-2 BREAKOUT)
        # =========================================================================
        # 20 Günlük Zirveden Sapma % (0% = Tam Kırıyor, +3% = Yeni Kırdı)
        pivot_dist = ((close - high_1m) / (high_1m + 1e-9)) * 100.0 if high_1m > 0 else 0.0
        
        # Kırılım Tazelik Puanı (Kırılım noktasına ne kadar yakınsa o kadar yüksek puan)
        # -%2 ile +%4 arasında olan hisseler 100 tam puan alır!
        pivot_score = max(100.0 - abs(pivot_dist - 1.0) * 12.0, 10.0)

        # 2. 20 Günlük Taban Sıkışma Genişliği (Dar Taban = Güçlü Enerji)
        base_width = ((high_1m - low_1m) / (close + 1e-9)) * 100.0 if close > 0 else 25.0
        tightness_score = max(100.0 - base_width * 2.5, 15.0)

        # =========================================================================
        # 3. AŞIRI ŞİŞME CEZASI (SON 1 HAFTADA %18+ KOŞANLARI AŞAĞI BASTIRIR)
        # =========================================================================
        # Son 1 haftada zaten çok primlenmiş hisselerin puanını kademeli kısar
        extension_penalty = 1.0
        if perf_w > 12.0:
            extension_penalty = max(1.0 - (perf_w - 12.0) * 0.04, 0.20)

        # 4. KURUMSAL SÜPÜRME VE HACİM
        range_span = high - low
        clv = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        sweep_ratio = (f_ratio * 0.40) + (max(clv, 0) * 60.0)
        sweep_ratio = round(min(max(sweep_ratio, 5.0), 98.5), 1)

        vol_z = float((rvol - 1.0) * 1.85)
        vol_z = round(min(max(vol_z, -2.0), 5.0), 2)

        quality_score = 50.0
        if roe >= 15.0: quality_score += 30.0
        if pb <= 5.0: quality_score += 20.0

        item['pivot_dist'] = round(pivot_dist, 1)
        item['base_width'] = round(base_width, 1)
        item['pivot_score'] = pivot_score
        item['tightness_score'] = tightness_score
        item['sweep_ratio'] = sweep_ratio
        item['vol_z'] = vol_z
        item['quality_score'] = quality_score
        item['extension_penalty'] = extension_penalty
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty: 
        return res_df

    # Yüzdelik Normalizasyon
    res_df['pct_pivot'] = res_df['pivot_score'].rank(pct=True) * 100.0
    res_df['pct_tight'] = res_df['tightness_score'].rank(pct=True) * 100.0
    res_df['pct_sweep'] = res_df['sweep_ratio'].rank(pct=True) * 100.0
    res_df['pct_vol'] = res_df['vol_z'].rank(pct=True) * 100.0

    # NİHAİ PUAN: %35 Kırılım Noktası + %25 Taban Sıkışması + %25 Süpürme + %15 Hacim
    raw_score = (
        res_df['pct_pivot'] * 0.35 + 
        res_df['pct_tight'] * 0.25 + 
        res_df['pct_sweep'] * 0.25 + 
        res_df['pct_vol'] * 0.15
    ) * res_df['extension_penalty']

    raw_score = np.round(np.clip(raw_score, 0.0, 99.5), 1)

    # Sadece o gün pozitif kapatanlar tam puan alır
    res_df['quant_score'] = np.where(
        res_df['change_%'] > 0.0,
        raw_score,
        np.round(raw_score * 0.15, 1)
    )

    # Rejim Tespiti
    conditions = [
        (res_df['perf_w'] > 20.0),
        (res_df['quant_score'] >= 75.0) & (res_df['pivot_dist'].between(-2.0, 4.0)),
        (res_df['quant_score'] >= 55.0),
        (res_df['change_%'] < -1.5)
    ]
    choices = [
        "🚫 TREN KAÇTI (HAFTALIK AŞIRI PRİM)",
        "🚀 TAZE TABAN KIRILIMI (DAY 1-2 PİVOT)",
        "⚡ KIRILIM ADAYI (SIKIŞMA)",
        "🚨 KURUMSAL BOŞALTIM (DUMP)"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR")

    drop_cols = ['pct_pivot', 'pct_tight', 'pct_sweep', 'pct_vol', 'pivot_score', 'tightness_score', 'extension_penalty']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    # Düne Göre Fark
    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)
