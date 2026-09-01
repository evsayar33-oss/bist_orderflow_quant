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

    if dynamic_weights is None:
        dynamic_weights = {"persistence": 0.40, "growth": 0.25, "sweep": 0.25, "vol_z": 0.10}

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
        perf_w = float(item.get('perf_w', 0.0))
        perf_1m = float(item.get('perf_1m', 0.0))
        perf_3m = float(item.get('perf_3m', 0.0))
        perf_6m = float(item.get('perf_6m', 0.0))
        perf_1y = float(item.get('perf_1y', 0.0))

        # =========================================================================
        # 1. KESİN DİSKALİFİYE FİLTRELERİ (AŞIRI PRİM & DÜŞEN BIÇAK)
        # =========================================================================
        is_disqualified = False
        disqualify_reason = "NÖTR"
        
        # Kural A: GÜNLÜK EKSİ KAPATANLAR ASLA LİSTEYE GİREMEZ!
        if change <= 0.0:
            is_disqualified = True
            disqualify_reason = "🚨 GÜNLÜK EKSİ / DÜŞÜŞTE"
            
        # Kural B: SON 1-2 HAFTADA ZATEN PATLAMIŞ OLANLAR (TRENİ KAÇANLAR)
        # Eğer son 1 haftada %18'den fazla prim yaptıysa yeni giriş için R:R çok kötüdür!
        elif perf_w > 18.0 or perf_1m > 45.0:
            is_disqualified = True
            disqualify_reason = "🚫 TREN KAÇTI (1-2 HAFTALIK AŞIRI PRİM)"
            
        # Kural C: Düşen Bıçak & Testere (6A < %15 veya 3A < %8)
        elif perf_6m < 15.0 or perf_3m < 8.0:
            is_disqualified = True
            disqualify_reason = "🪤 DÜŞEN BIÇAK / TESTERE"
            
        # Kural D: Zarar eden şirketler
        elif roe <= 0.0 or net_margin < 0.0:
            is_disqualified = True
            disqualify_reason = "🚨 ZOMBİ BİLANÇO"

        # =========================================================================
        # 2. DİNLENMİŞ ORTA VADELİ LİDERLİK SKORU
        # =========================================================================
        # İdeal Durum: 3A ve 6A çok güçlü (+), ama son 1-2 haftadır dinlenmiş (-%2 ile +%12 arası)
        trend_persistence = (
            max(perf_1m, 0.0) * 0.20 + 
            max(perf_3m, 0.0) * 0.40 + 
            max(perf_6m, 0.0) * 0.30 + 
            max(perf_1y * 0.1, 0.0) * 0.10
        )
        
        # 1-2 haftadır dinlenip yeni kalkan hisseye bonus
        if -2.0 <= perf_w <= 12.0 and perf_6m >= 15.0:
            trend_persistence *= 1.30 # Dinlenmiş lider bonusu

        growth_discount = (roe / max(pb, 0.3)) if roe > 0 else 0.0

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
        item['is_disqualified'] = is_disqualified
        item['disqualify_reason'] = disqualify_reason
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty: 
        return res_df

    # 3. NORMALİZASYON
    res_df['pct_persistence'] = res_df['trend_persistence'].rank(pct=True) * 100.0
    res_df['pct_growth'] = res_df['growth_discount'].rank(pct=True) * 100.0
    res_df['pct_sweep'] = res_df['sweep_ratio'].rank(pct=True) * 100.0
    res_df['pct_vol_z'] = res_df['vol_z'].rank(pct=True) * 100.0

    w_p = dynamic_weights.get('persistence', 0.40)
    w_g = dynamic_weights.get('growth', 0.25)
    w_s = dynamic_weights.get('sweep', 0.25)
    w_v = dynamic_weights.get('vol_z', 0.10)

    raw_score = np.round(
        res_df['pct_persistence'] * w_p + 
        res_df['pct_growth'] * w_g + 
        res_df['pct_sweep'] * w_s + 
        res_df['pct_vol_z'] * w_v, 
        1
    )
    
    # DİSKALİFİYE EDİLENLERİ KESİNLİKLE SIFIRLA!
    res_df['quant_score'] = np.where((~res_df['is_disqualified']), raw_score, 0.0)

    # Rejim Tespiti
    conditions = [
        res_df['is_disqualified'],
        (res_df['quant_score'] >= 75.0) & (res_df['perf_w'] <= 12.0),
        (res_df['quant_score'] >= 75.0)
    ]
    choices = [
        res_df['disqualify_reason'],
        "🚀 DİNLENMEDEN YENİ KALKAN LİDER (DAY-1/2)",
        "🏛️ ORTA VADELİ LİDER (RYGYO MODELİ)"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR")

    drop_cols = ['trend_persistence', 'is_disqualified', 'disqualify_reason']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    # Düne Göre Skor Farkı
    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)
