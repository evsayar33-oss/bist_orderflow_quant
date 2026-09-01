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
        
        high_6m = float(item.get('high_6m', close))
        low_52w = float(item.get('low_52w', close))
        perf_3m = float(item.get('perf_3m', 0.0))
        perf_6m = float(item.get('perf_6m', 0.0))
        roe = float(item.get('roe', 15.0))
        pb = float(item.get('pb', 2.0))

        # =========================================================================
        # 1. KESİN AGESA İNFAZ FİLTRESİ (DİPTEN UZAKLIK KONTROLÜ)
        # =========================================================================
        is_prior_runner = False
        
        # A. 52 Haftalık Dip Fiyattan Uzaklık (AGESA %35 uzaktaydı, infaz edilir!)
        dist_from_bottom = ((close - low_52w) / (low_52w + 1e-9)) * 100.0 if low_52w > 0 else 50.0
        if dist_from_bottom > 22.0:  # Dipten %22'den fazla uzaklaşmış olanlar ÖNCEDEN KOŞMUŞTUR!
            is_prior_runner = True
            
        # B. 3A veya 6A Prim Tavanı
        if perf_3m > 20.0 or perf_6m > 30.0:
            is_prior_runner = True

        # =========================================================================
        # 2. 6 AYLIK ZİRVE KIRILIMI & DİP SIKIŞMA PUANI
        # =========================================================================
        # 6 Aylık Zirveye Uzaklık (Tam bugün kırıyor mu?)
        pivot_6m_dist = ((close - high_6m) / (high_6m + 1e-9)) * 100.0 if high_6m > 0 else 0.0

        freshness_score = 0.0
        if -3.0 <= pivot_6m_dist <= 5.0 and dist_from_bottom <= 18.0:
            freshness_score += 80.0  # Gerçek Dipten İlk Kırılım!
            if dist_from_bottom <= 10.0:
                freshness_score += 20.0
        elif dist_from_bottom <= 15.0:
            freshness_score += 40.0

        # 3. MİKROYAPI SÜPÜRME
        range_span = high - low
        clv = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        sweep_ratio = (f_ratio * 0.40) + (max(clv, 0) * 60.0)
        sweep_ratio = round(min(max(sweep_ratio, 5.0), 98.5), 1)

        vol_z = float((rvol - 1.0) * 1.85)
        vol_z = round(min(max(vol_z, -2.0), 5.0), 2)

        quality_score = 50.0
        if roe >= 15.0: quality_score += 30.0
        if pb <= 5.0: quality_score += 20.0

        item['pivot_6m_dist'] = round(pivot_6m_dist, 1)
        item['dist_from_bottom'] = round(dist_from_bottom, 1)
        item['freshness_score'] = freshness_score
        item['sweep_ratio'] = sweep_ratio
        item['vol_z'] = vol_z
        item['quality_score'] = quality_score
        item['is_prior_runner'] = is_prior_runner
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty: 
        return res_df

    res_df['pct_fresh'] = res_df['freshness_score'].rank(pct=True) * 100.0
    res_df['pct_sweep'] = res_df['sweep_ratio'].rank(pct=True) * 100.0
    res_df['pct_vol'] = res_df['vol_z'].rank(pct=True) * 100.0
    res_df['pct_qual'] = res_df['quality_score'].rank(pct=True) * 100.0

    raw_score = np.round(
        res_df['pct_fresh'] * 0.45 + 
        res_df['pct_sweep'] * 0.25 + 
        res_df['pct_vol'] * 0.20 + 
        res_df['pct_qual'] * 0.10, 
        1
    )
    
    # AGESA TİPİ ÖNCEDEN KOŞANLARI SIFIRLA!
    res_df['quant_score'] = np.where(
        (res_df['change_%'] > 0.0) & (~res_df['is_prior_runner']) & (res_df['freshness_score'] >= 50.0),
        raw_score,
        0.0
    )

    # Rejim Tespiti
    conditions = [
        res_df['is_prior_runner'],
        (res_df['quant_score'] >= 70.0),
        (res_df['quant_score'] >= 50.0),
        (res_df['change_%'] < -1.5)
    ]
    choices = [
        "🚫 ÖNCEDEN KOŞMUŞ (AGESA TİPİ DİSKALİFİYE)",
        "🚀 DİPTEN İLK KIRILIM (STAGE-1 PRIMARY BASE)",
        "⚡ DİPTE SIKIŞMA (KIRILIM ADAYI)",
        "🚨 KURUMSAL BOŞALTIM (DUMP)"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR")

    drop_cols = ['pct_fresh', 'pct_sweep', 'pct_vol', 'pct_qual', 'quality_score', 'freshness_score', 'is_prior_runner']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    # Düne Göre Fark
    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)
