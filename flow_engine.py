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
        open_p = float(item.get('open', close))
        high = float(item.get('high', close))
        low = float(item.get('low', close))
        change = float(item.get('change_%', 0.0))
        value_traded = float(item.get('value_traded', 0.0))
        rvol = float(item.get('rvol', 1.0))
        f_ratio = float(item.get('foreign_ratio', 20.0))
        
        pe = float(item.get('pe', 999.0))
        pb = float(item.get('pb', 999.0))
        roe = float(item.get('roe', 0.0))
        roa = float(item.get('roa', 0.0))
        op_margin = float(item.get('op_margin', 0.0))
        net_margin = float(item.get('net_margin', 0.0))
        debt_to_equity = float(item.get('debt_to_equity', 99.0))
        rev_growth = float(item.get('rev_growth', 0.0))

        # =========================================================================
        # 1. ZOMBI ŞİRKET & İFLAS RİSKİ FİLTRESİ (FUNDAMENTAL TRASH KILLER)
        # =========================================================================
        is_fundamental_trash = False
        
        # Kural A: Zarar eden veya özsermaye karlılığı negatif olanlar (Geleceği yok)
        if roe <= 0.0 or net_margin < 0.0:
            is_fundamental_trash = True
            
        # Kural B: Borç Batağı (Borç / Özsermaye oranı %250'nin üzerindeyse)
        if debt_to_equity > 2.5:
            is_fundamental_trash = True
            
        # Kural C: Aşırı Şişmiş Balon Değerleme (F/K > 70 veya PD/DD > 20)
        if pe > 70.0 or pb > 20.0:
            is_fundamental_trash = True

        # =========================================================================
        # 2. GELECEK BÜYÜME VE DEĞERLEME İSKONTOSU (JUSTIFIED VALUATION)
        # =========================================================================
        # ROE / PB Oranı: Ne kadar sermaye verimli ve ne kadar ucuz? (RYGYO Modeli)
        growth_discount_ratio = (roe / (pb + 1e-9)) if pb > 0 else 0.0
        
        # Faaliyet Marjı ve Gelir Büyümesi Gücü
        margin_power = max(op_margin, 0.0) + max(rev_growth * 0.5, 0.0)

        # =========================================================================
        # 3. MİKROYAPI & SÜPÜRME (SWEEP RATIO)
        # =========================================================================
        range_span = high - low
        if range_span > 0:
            clv = ((close - low) - (high - close)) / range_span
        else:
            clv = 0.0
            
        sweep_ratio = (f_ratio * 0.50) + (max(clv, 0) * 50.0)
        sweep_ratio = round(min(max(sweep_ratio, 5.0), 98.5), 1)

        vol_z = float((rvol - 1.0) * 1.85)
        vol_z = round(min(max(vol_z, -2.0), 5.0), 2)

        item['growth_discount'] = round(growth_discount_ratio, 2)
        item['margin_power'] = round(margin_power, 2)
        item['sweep_ratio'] = sweep_ratio
        item['vol_z'] = vol_z
        item['is_fundamental_trash'] = is_fundamental_trash
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty: 
        return res_df

    # Yüzdelik Dilim Sıralaması
    res_df['pct_growth'] = res_df['growth_discount'].rank(pct=True) * 100.0
    res_df['pct_margin'] = res_df['margin_power'].rank(pct=True) * 100.0
    res_df['pct_sweep'] = res_df['sweep_ratio'].rank(pct=True) * 100.0
    res_df['pct_vol_z'] = res_df['vol_z'].rank(pct=True) * 100.0

    # GELECEK QUANT SKORU:
    # %35 Sermaye Verimlilik İskontosu (ROE/PB) + %25 Büyüme & Marj + %25 Süpürme + %15 Hacim
    raw_score = np.round(
        res_df['pct_growth'] * 0.35 + 
        res_df['pct_margin'] * 0.25 + 
        res_df['pct_sweep'] * 0.25 + 
        res_df['pct_vol_z'] * 0.15, 
        1
    )
    
    # Çöp Şirketleri Sıfırla
    res_df['quant_score'] = np.where(
        (res_df['change_%'] >= 0) & (~res_df['is_fundamental_trash']),
        raw_score,
        0.0
    )

    # Rejim Sınıflandırması
    conditions = [
        res_df['is_fundamental_trash'],
        (res_df['quant_score'] >= 75.0) & (res_df['growth_discount'] >= 15.0),
        (res_df['quant_score'] >= 55.0) & (res_df['pct_sweep'] >= 60.0),
        (res_df['change_%'] < -1.5) & (res_df['vol_z'] >= 0.5)
    ]
    choices = [
        "🚨 TEMEL ÇÖP (KÖTÜ BİLANÇO/BORÇ)",
        "🏛️ BİLEŞİK BÜYÜME ŞAMPİYONU (COMPOUNDER)",
        "⚡ KURUMSAL AKIŞ TOPLAMASI",
        "🚨 KURUMSAL BOŞALTIM (DUMP)"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR")

    drop_cols = ['pct_growth', 'pct_margin', 'pct_sweep', 'pct_vol_z', 'is_fundamental_trash']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    # Düne göre fark
    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)
