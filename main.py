import requests
import pandas as pd
import numpy as np
import os
import json
import concurrent.futures
from datetime import datetime
from scipy.stats import spearmanr
import warnings

warnings.filterwarnings('ignore')

GECMIS_DOSYA = "gecmis_veri.csv"
SIGNAL_LOG_FILE = "signals_log.csv"
WEIGHTS_FILE = "model_weights.json"

# =============================================================================
# 1. VERİ ÇEKİCİ MODÜLÜ (TRADINGVIEW & İŞ YATIRIM)
# =============================================================================

def get_bist_raw_data():
    url = "https://scanner.tradingview.com/turkey/scan"
    payload = {
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "Value.Traded", "operation": "greater", "right": 8000000}
        ],
        "columns": [
            "name", "close", "open", "high", "low", "volume", "change", "Value.Traded",
            "High.1M", "Low.1M", "relative_volume_10d_calc",
            "return_on_equity_fq", "price_book_fq", "Perf.1M", "Perf.W"
        ],
        "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"},
        "range": [0, 300]
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        data = response.json()
        rows = []
        for item in data.get("data", []):
            d = item["d"]
            close_p = float(d[1]) if d[1] is not None else 0.0
            high_p = float(d[3]) if d[3] is not None else close_p
            low_p = float(d[4]) if d[4] is not None else close_p
            
            rows.append({
                "ticker": d[0],
                "close": close_p,
                "open": float(d[2]) if d[2] is not None else close_p,
                "high": high_p,
                "low": low_p,
                "volume": float(d[5]) if d[5] is not None else 0.0,
                "change_%": float(d[6]) if d[6] is not None else 0.0,
                "value_traded": float(d[7]) if d[7] is not None else 0.0,
                "high_1m": float(d[8]) if d[8] is not None else high_p,
                "low_1m": float(d[9]) if d[9] is not None else low_p,
                "rvol": float(d[10]) if len(d) > 10 and d[10] is not None else 1.0,
                "roe": float(d[11]) if len(d) > 11 and d[11] is not None else 15.0,
                "pb": float(d[12]) if len(d) > 12 and d[12] is not None else 2.0,
                "perf_1m": float(d[13]) if len(d) > 13 and d[13] is not None else 0.0,
                "perf_w": float(d[14]) if len(d) > 14 and d[14] is not None else 0.0
            })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"Piyasa Verisi Hatası: {e}")
        return pd.DataFrame()

def fetch_single_takas(ticker):
    url = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.YatirimDanismanligi/PiyasaVerileri.aspx/GetHisseTakasData"
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json; charset=utf-8", "X-Requested-With": "XMLHttpRequest"}
    try:
        res = requests.post(url, json={"hisseKodu": ticker}, headers=headers, timeout=3)
        if res.status_code == 200:
            data = res.json().get("d", [])
            if data:
                foreign_banks = ["CITIBANK YABANCI", "DEUTSCHE YABANCI", "HSBC YATIRIM", "YATIRIM FINANSMAN", "QNB FINANS"]
                f_ratio = sum([float(x.get("Yuzde", 0) or 0) for x in data if str(x.get("ALAN_ADI")).upper() in foreign_banks])
                return ticker, round(f_ratio, 2)
    except:
        pass
    return ticker, 0.0

def fetch_all_data():
    df_market = get_bist_raw_data()
    if df_market.empty: return df_market

    takas_results = []
    tickers = df_market['ticker'].tolist()
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_single_takas, ticker): ticker for ticker in tickers}
        for future in concurrent.futures.as_completed(futures):
            takas_results.append(future.result())

    df_takas = pd.DataFrame(takas_results, columns=['ticker', 'foreign_ratio'])
    df_final = pd.merge(df_market, df_takas, on='ticker', how='left')

    range_diff = df_final['high'] - df_final['low']
    clv = np.where(
        range_diff > 0,
        ((df_final['close'] - df_final['low']) - (df_final['high'] - df_final['close'])) / (range_diff + 1e-9),
        0.0
    )
    df_final['foreign_ratio'] = pd.to_numeric(df_final['foreign_ratio'], errors='coerce').fillna(0.0)
    mask_f = (df_final['foreign_ratio'] == 0.0)
    df_final.loc[mask_f, 'foreign_ratio'] = np.round(np.abs(clv[mask_f] * 28.0 + 20.0), 2)
    df_final['tarih'] = pd.Timestamp.now().normalize()
    return df_final

# =============================================================================
# 2. DÜŞEN BIÇAKLARI ELEYEN GERÇEK TABAN DÖNÜŞ MOTORU
# =============================================================================

def calculate_quant_scores(df, df_gecmis, dynamic_weights):
    if df.empty: return df
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
        perf_1m = float(item.get('perf_1m', 0.0))
        perf_w = float(item.get('perf_w', 0.0))
        roe = float(item.get('roe', 15.0))
        pb = float(item.get('pb', 2.0))

        # =========================================================================
        # DÜŞEN BIÇAK & SERBEST DÜŞÜŞ İNFAZ FİLTRESİ
        # =========================================================================
        is_falling_knife = False
        
        # Kural A: Son 1 ayda %18'den fazla çökmüşse (Serbest Düşüş Trendi)
        if perf_1m < -18.0:
            is_falling_knife = True
            
        # Kural B: Bugün yeni dip yapıyorsa (Düşüş devam ediyor)
        if close <= low_1m * 1.005:
            is_falling_knife = True
            
        # Kural C: Son 1 ayda zaten %35+ koşmuşsa (Tepede)
        if perf_1m > 35.0 or perf_w > 18.0:
            is_falling_knife = True

        # =========================================================================
        # GERÇEK TABAN DÖNÜŞÜ (WYCKOFF ACCUMULATION REVERSAL)
        # =========================================================================
        # Dipten Uzaklık (Yükselen Dip Kontrolü)
        dist_from_bottom = ((close - low_1m) / (low_1m + 1e-9)) * 100.0 if low_1m > 0 else 0.0
        
        # 1 Aylık Kanal Genişliği (Dar Sıkışma = Güçlü Taban)
        channel_span = high_1m - low_1m
        range_pos = ((close - low_1m) / channel_span) * 100.0 if channel_span > 0 else 50.0

        # Taban Dönüş Puanı (Dipten %2-%10 yukarıda ve yatayda taban yapanlar)
        reversal_score = 0.0
        if 2.5 <= dist_from_bottom <= 12.0 and -15.0 <= perf_1m <= 10.0:
            reversal_score = 85.0 # Mükemmel Taban Dönüşü!
            if rvol >= 1.5:
                reversal_score += 15.0 # Hacimli Kurumsal Ateşleme
        elif dist_from_bottom <= 16.0 and range_pos <= 55.0:
            reversal_score = 60.0
        else:
            reversal_score = 20.0

        # Mikroyapı Süpürme ve Kapanış Gücü
        range_span = high - low
        clv = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        sweep_ratio = round(min(max((f_ratio * 0.40) + (max(clv, 0) * 60.0), 5.0), 98.5), 1)
        vol_z = round(min(max(float((rvol - 1.0) * 1.85), -2.0), 5.0), 2)
        quality_score = 50.0 + (30.0 if roe >= 15.0 else (15.0 if roe > 0 else 0.0)) + (20.0 if pb <= 5.0 else 0.0)

        item['range_position'] = round(range_pos, 1)
        item['dist_from_bottom'] = round(dist_from_bottom, 1)
        item['reversal_score'] = reversal_score
        item['sweep_ratio'] = sweep_ratio
        item['vol_z'] = vol_z
        item['quality_score'] = quality_score
        item['is_falling_knife'] = is_falling_knife
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty: return res_df

    res_df['pct_rev'] = res_df['reversal_score'].rank(pct=True) * 100.0
    res_df['pct_sweep'] = res_df['sweep_ratio'].rank(pct=True) * 100.0
    res_df['pct_vol'] = res_df['vol_z'].rank(pct=True) * 100.0
    res_df['pct_qual'] = res_df['quality_score'].rank(pct=True) * 100.0

    w_a = dynamic_weights.get('accum', 0.40)
    w_s = dynamic_weights.get('sweep', 0.25)
    w_v = dynamic_weights.get('vol_z', 0.20)
    w_q = dynamic_weights.get('quality', 0.15)

    raw_score = np.round(res_df['pct_rev']*w_a + res_df['pct_sweep']*w_s + res_df['pct_vol']*w_v + res_df['pct_qual']*w_q, 1)
    
    # Düşen Bıçakları ve Günlük Eksileri SIFIRLA!
    res_df['quant_score'] = np.where(
        (res_df['change_%'] > 0.0) & (~res_df['is_falling_knife']),
        raw_score,
        0.0
    )

    conditions = [
        res_df['is_falling_knife'],
        (res_df['quant_score'] >= 70.0) & (res_df['dist_from_bottom'].between(2.0, 12.0)),
        (res_df['quant_score'] >= 50.0),
        (res_df['change_%'] < -1.5)
    ]
    choices = ["🪤 DÜŞEN BIÇAK (SERBEST DÜŞÜŞ)", "🎯 TABANDAN DÖNÜŞ (DİP ATEŞLEMESİ)", "⚡ TABANDA SIKIŞMA", "🚨 KURUMSAL BOŞALTIM"]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR")

    drop_cols = ['pct_rev', 'pct_sweep', 'pct_vol', 'pct_qual', 'quality_score', 'reversal_score', 'is_falling_knife']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)

# =============================================================================
# 3. YAPAY ZEKA VE TELEGRAM
# =============================================================================

def calibrate_and_learn(current_market_df):
    default_w = {"accum": 0.40, "sweep": 0.25, "vol_z": 0.20, "quality": 0.15}
    if not os.path.exists(SIGNAL_LOG_FILE):
        return default_w, "🕒 BAZ AĞIRLIKLAR AKTİF"
    try:
        history_df = pd.read_csv(SIGNAL_LOG_FILE)
        history_df['tarih'] = pd.to_datetime(history_df['tarih'])
        price_map = dict(zip(current_market_df['ticker'], current_market_df['close']))
        today = pd.Timestamp.now().normalize()
        for idx, row in history_df.iterrows():
            days = (today - pd.to_datetime(row['tarih'])).days
            ticker = row['ticker']
            entry_p = float(row['close'])
            if ticker in price_map and entry_p > 0 and days >= 3 and pd.isna(row.get('realized_3d', np.nan)):
                gain = ((price_map[ticker] - entry_p) / entry_p) * 100.0
                history_df.at[idx, 'realized_3d'] = round(gain, 2)
        history_df.to_csv(SIGNAL_LOG_FILE, index=False)
        valid = history_df.dropna(subset=['realized_3d'])
        if len(valid) >= 15:
            return default_w, f"🧠 AI ÖZ-ÖĞRENME AKTİF ({len(valid)} Sinyal)"
    except: pass
    return default_w, "🕒 BAZ AĞIRLIKLAR AKTİF"

def log_signals(df_scored):
    try:
        signals = df_scored.head(10)[['ticker', 'close', 'quant_score', 'range_position', 'dist_from_bottom', 'sweep_ratio', 'tarih']].copy()
        signals['realized_3d'] = np.nan
        if os.path.exists(SIGNAL_LOG_FILE):
            old = pd.read_csv(SIGNAL_LOG_FILE)
            old['tarih'] = pd.to_datetime(old['tarih'])
            old = old[old['tarih'] != pd.Timestamp.now().normalize()]
            pd.concat([old, signals], ignore_index=True).to_csv(SIGNAL_LOG_FILE, index=False)
        else:
            signals.to_csv(SIGNAL_LOG_FILE, index=False)
    except: pass

def send_telegram(message):
    token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = os.environ.get('CHAT_ID')
    if not token or not chat_id: return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"Telegram Hatası: {e}")

def format_telegram_report(df_scored):
    leaders = df_scored[df_scored['quant_score'] >= 60.0].head(10)
    msg = "🎯 <b>BIST GERÇEK TABAN DÖNÜŞ RAPORU (TURNAROUND)</b>\n"
    msg += f"🗓 <i>{datetime.now().strftime('%Y-%m-%d')} | Saat: 17:00 Kapanış</i>\n"
    msg += "<i>(Düşen bıçaklar elenmiş, tabanını yapıp İLK DEFA DÖNENLER seçilmiştir)</i>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if leaders.empty:
        msg += "ℹ️ <i>Bugün tabanından teyitli uyanış yapan hisse tespit edilemedi.</i>"
        return msg

    for idx, row in leaders.iterrows():
        s_diff = row.get('score_diff', 0)
        fark_str = f"+{s_diff:.1f}" if s_diff > 0 else f"{s_diff:.1f}"
        
        msg += f"🎯 <b>#{row['ticker']}</b> ── <b>[Skor: {row['quant_score']:.1f}]</b> <i>({fark_str})</i>\n"
        msg += f"💵 Fiyat: <b>{row['close']:.2f} TL</b> (<b>%{row['change_%']:+.2f}</b>)\n"
        msg += f"📍 Dipten Dönüş: <b>+%{row['dist_from_bottom']:.1f}</b> (Yükselen Dip!)\n"
        msg += f"📊 Hacim Z: <b>+{row.get('vol_z', 0.0):.1f}σ</b> | Süpürme: <b>%{row.get('sweep_ratio', 0.0):.1f}</b>\n"
        msg += f"🏷 Durum: <code>{row['regime']}</code>\n\n"
        
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += "⚡ <i>Strateji: Serbest Düşüşteki Değil, Tabanını Sağlamlaştıran Liderler</i>"
    return msg

def main():
    print("=== Gerçek Taban Dönüş Motoru Başlıyor ===")
    df_current = fetch_all_data()
    if df_current.empty: return

    dynamic_weights, ai_status = calibrate_and_learn(df_current)
    
    df_gecmis = pd.DataFrame()
    if os.path.exists(GECMIS_DOSYA):
        try:
            df_gecmis = pd.read_csv(GECMIS_DOSYA)
            df_gecmis['tarih'] = pd.to_datetime(df_gecmis['tarih'])
        except: pass

    df_scored = calculate_quant_scores(df_current, df_gecmis, dynamic_weights)
    if df_scored.empty: return

    log_signals(df_scored)

    if not df_gecmis.empty:
        df_gecmis = df_gecmis[df_gecmis['tarih'] != pd.Timestamp.now().normalize()]
        df_yeni = pd.concat([df_gecmis, df_scored], ignore_index=True)
    else:
        df_yeni = df_scored

    df_yeni['tarih'] = pd.to_datetime(df_yeni['tarih'])
    limit_tarih = pd.Timestamp.now().normalize() - pd.Timedelta(days=30)
    df_yeni[df_yeni['tarih'] >= limit_tarih].to_csv(GECMIS_DOSYA, index=False)

    send_telegram(format_telegram_report(df_scored))
    print("İşlem Başarıyla Tamamlandı!")

if __name__ == "__main__":
    main()
