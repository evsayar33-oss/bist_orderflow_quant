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
        open_p = float(item.get('open', close))
        high = float(item.get('high', close))
        low = float(item.get('low', close))
        change = float(item.get('change_%', 0.0))
        value_traded = float(item.get('value_traded', 0.0))
        rvol = float(item.get('rvol', 1.0))
        f_ratio = float(item.get('foreign_ratio', 20.0))

        # =========================================================================
        # 1. LOGARİTMİK KYLE'S LAMBDA (UÇ DEĞERLERİ EZMEYEN FORMÜL)
        # =========================================================================
        raw_lambda = (abs(change) / ((value_traded / 10000000.0) + 1e-9)) if value_traded > 0 else 0.0
        # Logaritmik yumuşatma ile uç değerlerin piyasayı ezmesini engelliyoruz
        log_lambda = np.log1p(raw_lambda)

        # =========================================================================
        # 2. DİNAMİK KURUMSAL SÜPÜRME ORANI (SWEEP RATIO)
        # =========================================================================
        range_span = high - low
        if range_span > 0:
            clv = ((close - low) - (high - close)) / range_span
            body_efficiency = (close - open_p) / range_span
        else:
            clv = 0.0
            body_efficiency = 0.0
            
        # Süpürme Oranı: Kurum Payı (%40) + Kapanış Gücü (%35) + Gövde İvmesi (%25)
        sweep_ratio = (f_ratio * 0.40) + (max(clv, 0) * 35.0) + (max(body_efficiency, 0) * 25.0)
        sweep_ratio = round(min(max(sweep_ratio, 5.0), 98.5), 1)

        # =========================================================================
        # 3. İLK GÜN DE ÇALIŞAN ARTIK HACİM Z-SKORU
        # =========================================================================
        # TradingView'in 10 günlük RVOL verisini Z-Skoruna dönüştürür
        vol_z = float((rvol - 1.0) * 1.85)
        vol_z = round(min(max(vol_z, -2.0), 5.0), 2)

        item['sweep_ratio'] = sweep_ratio
        item['kyle_lambda'] = round(raw_lambda, 2)
        item['log_lambda'] = log_lambda
        item['vol_z'] = vol_z
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty: 
        return res_df

    # =========================================================================
    # 4. YÜZDELİK DİLİM NORMALİZASYONU (PERCENTILE RANKING)
    # =========================================================================
    # Tüm piyasayı kendi içinde 0 - 100 dilimine yayıyoruz (Liderler 90+ alır)
    res_df['pct_sweep'] = res_df['sweep_ratio'].rank(pct=True) * 100.0
    res_df['pct_vol_z'] = res_df['vol_z'].rank(pct=True) * 100.0
    res_df['pct_lambda'] = res_df['log_lambda'].rank(pct=True) * 100.0

    # Nihai Akış Skoru
    res_df['quant_score'] = np.where(
        res_df['change_%'] >= 0,
        np.round(res_df['pct_sweep'] * 0.40 + res_df['pct_vol_z'] * 0.35 + res_df['pct_lambda'] * 0.25, 1),
        np.round(res_df['pct_sweep'] * 0.10, 1) # Negatif fiyatlılara düşük taban puanı
    )

    # =========================================================================
    # 5. DİNAMİK REJİM ETİKETLEMESİ
    # =========================================================================
    conditions = [
        (res_df['quant_score'] >= 75.0) & (res_df['change_%'] > 1.0),
        (res_df['pct_lambda'] >= 75.0) & (res_df['change_%'] > 0.5),
        (res_df['change_%'] < -1.5) & (res_df['vol_z'] >= 0.5)
    ]
    choices = [
        "🏛️ KURUMSAL SÜPÜRME (SWEEP)",
        "⚡ LİKİDİTE BOŞLUĞU (VACUUM)",
        "🚨 KURUMSAL BOŞALTIM (DUMP)"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR AKIŞ")

    # Temizlik
    drop_cols = ['log_lambda', 'pct_sweep', 'pct_vol_z', 'pct_lambda']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    # Düne göre akış farkı
    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)
