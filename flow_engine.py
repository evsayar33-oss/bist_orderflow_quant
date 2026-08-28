import numpy as np
import pandas as pd
import os
import warnings

warnings.filterwarnings('ignore')

GECMIS_DOSYA = "gecmis_veri.csv"
HIST_WINDOW = 20

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
        value_traded = float(item.get('value_traded', 0.0))
        f_ratio = float(item.get('foreign_ratio', 20.0))
        rvol = float(item.get('rvol', 1.0))
        
        hist_df = df_gecmis[df_gecmis['ticker'] == item['ticker']] if not df_gecmis.empty else pd.DataFrame()

        # 1. KYLE'S LAMBDA (LİKİDİTE BOŞLUĞU: 10M TL BAŞINA FİYAT ETKİSİ)
        kyle_lambda = (abs(change) / ((value_traded / 10000000.0) + 1e-9)) if value_traded > 0 else 0.0
        kyle_lambda = round(min(kyle_lambda, 50.0), 3)

        # 2. KURUMSAL SÜPÜRME ORANI (SWEEP RATIO)
        range_span = high - low
        aggressor_score = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        sweep_ratio = (f_ratio * 0.55) + (max(aggressor_score, 0) * 45.0)
        sweep_ratio = round(min(max(sweep_ratio, 0.0), 100.0), 1)

        # 3. ARTIK HACİM ŞOKU
        if not hist_df.empty and 'value_traded' in hist_df.columns:
            hist_vals = hist_df['value_traded'].tail(HIST_WINDOW)
            mean_val = float(hist_vals.mean())
            std_val = float(hist_vals.std()) if len(hist_vals) > 2 and float(hist_vals.std()) > 0 else (mean_val * 0.3)
            vol_z = float((value_traded - mean_val) / (std_val + 1e-9))
        else:
            vol_z = float((rvol - 1.0) * 2.0)
        vol_z = round(min(max(vol_z, -2.0), 5.0), 2)

        item['sweep_ratio'] = sweep_ratio
        item['kyle_lambda'] = kyle_lambda
        item['vol_z'] = vol_z
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty: 
        return res_df

    # =========================================================================
    # 4. ÇAPRAZ KESİT NORMALİZASYONU (CROSS-SECTIONAL SCALING)
    # =========================================================================
    for col in ['sweep_ratio', 'vol_z', 'kyle_lambda']:
        min_v = float(res_df[col].min())
        max_v = float(res_df[col].max())
        if max_v - min_v > 0:
            res_df[f'{col}_norm'] = ((res_df[col] - min_v) / (max_v - min_v)) * 100.0
        else:
            res_df[f'{col}_norm'] = 50.0

    # Nihai Skor (%40 Süpürme + %35 Hacim Şoku + %25 Fiyat Etkisi)
    res_df['quant_score'] = np.where(
        res_df['change_%'] >= 0,
        np.round(res_df['sweep_ratio_norm'] * 0.40 + res_df['vol_z_norm'] * 0.35 + res_df['kyle_lambda_norm'] * 0.25, 1),
        np.round(res_df['sweep_ratio_norm'] * 0.10, 1)
    )

    # Rejim Tespiti
    conditions = [
        (res_df['quant_score'] >= 65.0) & (res_df['change_%'] > 1.0),
        (res_df['kyle_lambda_norm'] >= 65.0) & (res_df['change_%'] > 0),
        (res_df['change_%'] < -1.5) & (res_df['vol_z'] >= 0.5)
    ]
    choices = [
        "🏛️ KURUMSAL SÜPÜRME (SWEEP)",
        "⚡ LİKİDİTE BOŞLUĞU (VACUUM)",
        "🚨 KURUMSAL BOŞALTIM (DUMP)"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR AKIŞ")

    drop_cols = ['sweep_ratio_norm', 'vol_z_norm', 'kyle_lambda_norm']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    # Düne göre akış farkı
    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)
