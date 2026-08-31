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

def calculate_quant_scores(df, df_gecmis):
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
        
        pe = float(item.get('pe', 15.0))
        pb = float(item.get('pb', 2.0))
        roe = float(item.get('roe', 15.0))
        net_margin = float(item.get('net_margin', 8.0))
        perf_1m = float(item.get('perf_1m', 0.0))
        perf_3m = float(item.get('perf_3m', 0.0))
        perf_6m = float(item.get('perf_6m', 0.0))
        perf_1y = float(item.get('perf_1y', 0.0))

        # =========================================================================
        # 1. DEĞER TUZAĞI & DÜŞEN BIÇAK İNFAZ FİLTRESİ (ALARK & EDIP ENGELİ)
        # =========================================================================
        is_value_trap = False
        
        # Kural A: Son 3 ayda veya 6 ayda negatif getiri yapmışsa (Düşen Bıçak)
        if perf_3m < 0.0 or perf_6m < -5.0:
            is_value_trap = True
            
        # Kural B: 1 Yıllık trendi zayıfsa ve 1 ayda eksiye geçmişse (Tükeniş)
        if perf_1y < 0.0 and perf_1m < -3.0:
            is_value_trap = True
            
        # Kural C: Zarar eden veya negatif ROE'li şirketler
        if roe <= 0.0 or net_margin < 0.0:
            is_value_trap = True

        # =========================================================================
        # 2. ORTA VADELİ LİDERLİK GÜCÜ (STAGE-2 TREND & COMPOUNDING)
        # =========================================================================
        # Trend Süreklilik Skoru: 1A, 3A, 6A ve 1Y getirilerinin uyumu
        trend_persistence = max(perf_1m, 0) * 0.2 + max(perf_3m, 0) * 0.3 + max(perf_6m, 0) * 0.3 + max(perf_1y * 0.1, 0) * 0.2
        
        # Gordon Değerleme İskontosu (ROE / PB)
        growth_discount = (roe / max(pb, 0.3)) if roe > 0 else 0.0

        # Mikroyapı Süpürme
        range_span = high - low
        clv = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        sweep_ratio = (f_ratio * 0.50) + (max(clv, 0) * 50.0)
        sweep_ratio = round(min(max(sweep_ratio, 5.0), 98.5), 1)

        vol_z = float((rvol - 1.0) * 1.85)
        vol_z = round(min(max(vol_z, -2.0), 5.0), 2)

        item['trend_persistence'] = round(trend_persistence, 1)
        item['growth_discount'] = round(growth_discount, 2)
        item['sweep_ratio'] = sweep_ratio
        item['vol_z'] = vol_z
        item['is_value_trap'] = is_value_trap
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty: 
        return res_df

    # Çapraz Kesit Sıralaması
    res_df['pct_persistence'] = res_df['trend_persistence'].rank(pct=True) * 100.0
    res_df['pct_growth'] = res_df['growth_discount'].rank(pct=True) * 100.0
    res_df['pct_sweep'] = res_df['sweep_ratio'].rank(pct=True) * 100.0
    res_df['pct_vol_z'] = res_df['vol_z'].rank(pct=True) * 100.0

    # LİDERLİK SKORU: %40 Orta Vadeli Trend Sürekliliği + %30 ROE İskontosu + %20 Süpürme + %10 Hacim
    raw_score = np.round(
        res_df['pct_persistence'] * 0.40 + 
        res_df['pct_growth'] * 0.30 + 
        res_df['pct_sweep'] * 0.20 + 
        res_df['pct_vol_z'] * 0.10, 
        1
    )
    
    # Değer Tuzaklarını SIFIRLA
    res_df['quant_score'] = np.where(
        (~res_df['is_value_trap']),
        raw_score,
        0.0
    )

    # Rejim Sınıflandırması
    conditions = [
        res_df['is_value_trap'],
        (res_df['quant_score'] >= 75.0) & (res_df['perf_3m'] >= 10.0),
        (res_df['quant_score'] >= 55.0),
        (res_df['change_%'] < -2.0) & (res_df['vol_z'] >= 0.5)
    ]
    choices = [
        "🪤 DEĞER TUZAĞI (ALARK MODELİ DÜŞEN BIÇAK)",
        "🏛️ ORTA VADELİ LİDER (RYGYO MODELİ)",
        "⚡ POZİTİF TREND & AKIŞ",
        "🚨 KURUMSAL BOŞALTIM (DUMP)"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR")

    drop_cols = ['pct_persistence', 'pct_growth', 'pct_sweep', 'pct_vol_z', 'is_value_trap', 'trend_persistence']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    # Düne göre fark
    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)
