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

    total_market_value = df['value_traded'].sum() + 1e-9
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

        # 1. KYLE'S LAMBDA (LİKİDİTE BOŞLUĞU & FİYAT ETKİSİ)
        kyle_lambda = (abs(change) / ((value_traded / 10000000.0) + 1e-9)) if value_traded > 0 else 0.0
        kyle_lambda = round(min(kyle_lambda, 50.0), 3)

        # 2. KURUMSAL SÜPÜRME ORANI (SWEEP RATIO)
        range_span = high - low
        aggressor_score = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        sweep_ratio = (f_ratio * 0.6) + (max(aggressor_score, 0) * 40.0)
        sweep_ratio = round(min(max(sweep_ratio, 0.0), 100.0), 1)

        # 3. PİYASA BETASINDAN ARINDIRILMIŞ ARTIK HACİM ŞOKU
        if not hist_df.empty and 'value_traded' in hist_df.columns:
            hist_vals = hist_df['value_traded'].tail(HIST_WINDOW)
            mean_val = float(hist_vals.mean())
            std_val = float(hist_vals.std()) if len(hist_vals) > 2 and float(hist_vals.std()) > 0 else (mean_val * 0.3)
            vol_z = float((value_traded - mean_val) / (std_val + 1e-9))
        else:
            vol_z = float((rvol - 1.0) * 2.0)
        vol_z = round(min(max(vol_z, -2.0), 5.0), 2)

        # 4. NİHAİ ŞOK SKORU
        norm_vol_z = min(max((vol_z + 1.0) / 4.0 * 100.0, 0.0), 100.0)
        norm_lambda = min((kyle_lambda / 15.0) * 100.0, 100.0)
        
        if change >= 0:
            quant_score = (sweep_ratio * 0.40) + (norm_vol_z * 0.35) + (norm_lambda * 0.25)
        else:
            quant_score = 0.0
        quant_score = round(min(max(quant_score, 0.0), 100.0), 1)

        # Rejim Sınıflandırması
        if quant_score >= 70.0 and sweep_ratio >= 55.0 and vol_z >= 1.5:
            regime = "🏛️ KURUMSAL SÜPÜRME (SWEEP)"
        elif quant_score >= 50.0 and kyle_lambda >= 3.0:
            regime = "⚡ LİKİDİTE BOŞLUĞU (VACUUM)"
        elif change < -2.0 and vol_z >= 1.5:
            regime = "🚨 KURUMSAL BOŞALTIM (DUMP)"
        else:
            regime = "NÖTR AKIŞ"

        item['quant_score'] = quant_score
        item['sweep_ratio'] = sweep_ratio
        item['kyle_lambda'] = kyle_lambda
        item['vol_z'] = vol_z
        item['regime'] = regime
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty: 
        return res_df

    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)
