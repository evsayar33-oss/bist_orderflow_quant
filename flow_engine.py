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
        
        pb = float(item.get('pb', 2.0))
        roe = float(item.get('roe', 15.0))
        net_margin = float(item.get('net_margin', 8.0))
        perf_1m = float(item.get('perf_1m', 0.0))
        perf_3m = float(item.get('perf_3m', 0.0))
        perf_6m = float(item.get('perf_6m', 0.0))
        perf_1y = float(item.get('perf_1y', 0.0))

        # =========================================================================
        # 1. DEĞER TUZAĞI VE AŞIRI ŞİŞME KALKANI
        # =========================================================================
        is_value_trap = False
        is_overextended = False
        
        # Kural A: Düşen Bıçak (3A veya 6A getirisi eksi olanlar)
        if perf_3m < 0.0 or perf_6m < -5.0:
            is_value_trap = True
            
        # Kural B: TREN KAÇMIŞ / AŞIRI ŞİŞME (Son 1 ayda zaten %25'ten fazla fırlamışlar!)
        # Bu kural seni son 30 günde zaten tavan tavan gitmiş hisselerin tepesinde yakalanmaktan korur!
        if perf_1m > 25.0:
            is_overextended = True
            
        # Kural C: Zarar eden şirketler
        if roe <= 0.0 or net_margin < 0.0:
            is_value_trap = True

        # =========================================================================
        # 2. DİNLENMİŞ LİDERLİK SKORU (CONSOLIDATION BREAKOUT)
        # =========================================================================
        # İdeal Profil: 6A ve 3A çok güçlü (+), ama son 1 ayda dinlenmiş (%0 - %18 arası)
        consolidation_bonus = 0.0
        if 0.0 <= perf_1m <= 18.0 and perf_6m >= 15.0:
            consolidation_bonus = 40.0 # Dinlenip yeni kalkacaklara devasa bonus
        elif 18.0 < perf_1m <= 25.0:
            consolidation_bonus = 20.0
            
        # Gordon Değerleme İskontosu (ROE / PB)
        growth_discount = (roe / max(pb, 0.3)) if roe > 0 else 0.0

        # Mikroyapı Süpürme
        range_span = high - low
        clv = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        sweep_ratio = (f_ratio * 0.50) + (max(clv, 0) * 50.0)
        sweep_ratio = round(min(max(sweep_ratio, 5.0), 98.5), 1)

        vol_z = float((rvol - 1.0) * 1.85)
        vol_z = round(min(max(vol_z, -2.0), 5.0), 2)

        item['growth_discount'] = round(growth_discount, 2)
        item['sweep_ratio'] = sweep_ratio
        item['vol_z'] = vol_z
        item['consolidation_bonus'] = consolidation_bonus
        item['is_value_trap'] = is_value_trap
        item['is_overextended'] = is_overextended
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty: 
        return res_df

    # Çapraz Kesit Sıralaması
    res_df['pct_growth'] = res_df['growth_discount'].rank(pct=True) * 100.0
    res_df['pct_sweep'] = res_df['sweep_ratio'].rank(pct=True) * 100.0
    res_df['pct_vol_z'] = res_df['vol_z'].rank(pct=True) * 100.0

    # LİDERLİK SKORU: %35 Büyüme İskontosu + %30 Süpürme + %25 Dinlenme Bonusu + %10 Hacim
    raw_score = np.round(
        res_df['pct_growth'] * 0.35 + 
        res_df['pct_sweep'] * 0.30 + 
        res_df['consolidation_bonus'] * 0.25 + 
        res_df['pct_vol_z'] * 0.10, 
        1
    )
    
    # Değer Tuzaklarını ve Aşırı Şişmiş Tepeleri SIFIRLA
    res_df['quant_score'] = np.where(
        (~res_df['is_value_trap']) & (~res_df['is_overextended']),
        raw_score,
        0.0
    )

    # Rejim Sınıflandırması
    conditions = [
        res_df['is_overextended'],
        res_df['is_value_trap'],
        (res_df['quant_score'] >= 70.0) & (res_df['consolidation_bonus'] > 0),
        (res_df['quant_score'] >= 50.0),
        (res_df['change_%'] < -2.0) & (res_df['vol_z'] >= 0.5)
    ]
    choices = [
        "🚫 TREN KAÇMIŞ (SON 1A > %25 AŞIRI PRİMLİ)",
        "🪤 DEĞER TUZAĞI (DÜŞEN BIÇAK)",
        "🚀 DİNLENMEDEN YENİ ATEŞLENEN LİDER",
        "⚡ POZİTİF TREND & AKIŞ",
        "🚨 KURUMSAL BOŞALTIM (DUMP)"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR")

    drop_cols = ['pct_growth', 'pct_sweep', 'pct_vol_z', 'consolidation_bonus', 'is_value_trap', 'is_overextended']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    # Düne göre fark
    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)
