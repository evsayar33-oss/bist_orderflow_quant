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
        
        pe = float(item.get('pe', 15.0))
        pb = float(item.get('pb', 2.0))
        roe = float(item.get('roe', 15.0))
        op_margin = float(item.get('op_margin', 10.0))
        net_margin = float(item.get('net_margin', 8.0))
        rev_growth = float(item.get('rev_growth', 20.0))

        # =========================================================================
        # 1. GORDON GELECEK BÜYÜME İSKONTOSU (JUSTIFIED VALUATION)
        # =========================================================================
        # ROE / PB Oranı: Sermaye karlılığına göre en ucuz ve en kaliteli olanlar (RYGYO Modeli)
        safe_pb = max(pb, 0.2)
        growth_discount = (roe / safe_pb) if roe > 0 else (roe * 0.5)
        
        # 2. Operasyonel Karlılık & Marj Gücü
        margin_quality = max(op_margin, 0.0) + max(net_margin, 0.0) + max(rev_growth * 0.3, 0.0)

        # 3. F/K Değerleme Puanı (Düşük ve Pozitif F/K Ödülü)
        pe_score = (1.0 / max(pe, 1.0)) * 100.0 if pe > 0 else 0.0

        # 4. Kurumsal Süpürme & Mikroyapı
        range_span = high - low
        clv = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        sweep_ratio = (f_ratio * 0.50) + (max(clv, 0) * 50.0)
        sweep_ratio = round(min(max(sweep_ratio, 5.0), 98.5), 1)

        vol_z = float((rvol - 1.0) * 1.85)
        vol_z = round(min(max(vol_z, -2.0), 5.0), 2)

        item['growth_discount'] = round(growth_discount, 2)
        item['margin_quality'] = round(margin_quality, 2)
        item['pe_score'] = round(pe_score, 2)
        item['sweep_ratio'] = sweep_ratio
        item['vol_z'] = vol_z
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty: 
        return res_df

    # =========================================================================
    # 5. ÇAPRAZ KESİT FAKTÖR NORMALİZASYONU (PERCENTILE RANKING)
    # =========================================================================
    res_df['pct_growth'] = res_df['growth_discount'].rank(pct=True) * 100.0
    res_df['pct_margin'] = res_df['margin_quality'].rank(pct=True) * 100.0
    res_df['pct_pe'] = res_df['pe_score'].rank(pct=True) * 100.0
    res_df['pct_sweep'] = res_df['sweep_ratio'].rank(pct=True) * 100.0

    # Nihai Gelecek Quant Skoru:
    # %35 Gelecek Büyüme İskontosu (ROE/PB) + %25 Marj & Gelir Gücü + %20 F/K + %20 Kurumsal Süpürme
    res_df['quant_score'] = np.round(
        res_df['pct_growth'] * 0.35 + 
        res_df['pct_margin'] * 0.25 + 
        res_df['pct_pe'] * 0.20 + 
        res_df['pct_sweep'] * 0.20, 
        1
    )

    # Rejim Sınıflandırması
    conditions = [
        (res_df['roe'] <= 0) | (res_df['net_margin'] < -5.0),
        (res_df['quant_score'] >= 75.0) & (res_df['growth_discount'] >= 10.0),
        (res_df['quant_score'] >= 55.0),
        (res_df['change_%'] < -2.0) & (res_df['vol_z'] >= 1.0)
    ]
    choices = [
        "🚨 ZOMBİ / DÜŞÜK KALİTE",
        "🏛️ BİLEŞİK BÜYÜME ŞAMPİYONU (COMPOUNDER)",
        "⚡ SAĞLAM BİLANÇO & AKIŞ",
        "🚨 KURUMSAL BOŞALTIM (DUMP)"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR KALİTE")

    drop_cols = ['pct_growth', 'pct_margin', 'pct_pe', 'pct_sweep', 'pe_score', 'margin_quality']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    # Düne göre fark
    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)
