import requests
import pandas as pd
import numpy as np
import os
import concurrent.futures
from datetime import datetime
import warnings

from state_manager import load_ai_state, load_lifecycle_signals, LIFECYCLE_LOG_FILE
from longterm_auditor import audit_and_calibrate

warnings.filterwarnings('ignore')

GECMIS_DOSYA = "gecmis_veri.csv"

# =============================================================================
# 1. PİYASA VE TAKAS VERİSİ TOPLAMA
# =============================================================================

def get_bist_raw_data():
    """TradingView tarayıcısından piyasa, temel ve kanal verilerini çeker."""
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
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
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
        print(f"⚠️ Piyasa Verisi Hatası: {e}")
        return pd.DataFrame()

def fetch_single_takas(ticker):
    """İş Yatırım API üzerinden yabancı ve kurumsal pay oranını çeker."""
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
    except Exception:
        pass
    return ticker, 0.0

def fetch_all_market_data():
    """Piyasa verilerini ve kurumsal takas akışını birleştirir."""
    df_market = get_bist_raw_data()
    if df_market.empty:
        return df_market

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
# 2. DİNAMİK VE REJİM DUYARLI QUANT PUANLAMA MOTORU
# =============================================================================

def calculate_quant_scores(df, df_gecmis, state):
    if df.empty:
        return df

    weights = state.get("weights", {"accum": 0.35, "fundamental": 0.30, "sweep": 0.20, "vol_mom": 0.15})
    thresholds = state.get("thresholds", {})
    
    min_roe_floor = thresholds.get("min_roe_floor", 5.0)
    max_rel_knife = thresholds.get("max_relative_knife_drop", -14.0)
    max_range_pos = thresholds.get("max_range_position", 40.0)
    min_dist = thresholds.get("min_dist_from_bottom", 1.5)

    market_perf_median = float(df['perf_1m'].median())
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

        # A. DİNAMİK KANAL VE DİP GEOMETRİSİ
        channel_span = high_1m - low_1m
        range_pos = ((close - low_1m) / channel_span) * 100.0 if channel_span > 0 else 50.0
        dist_from_bottom = ((close - low_1m) / (low_1m + 1e-9)) * 100.0 if low_1m > 0 else 0.0
        
        stop_price = round(low_1m * 0.985, 2)
        target_price = round(close + max(channel_span * 0.70, close * 0.15), 2)

        # B. ENDEKS RÖLATİF DÜŞEN BIÇAK FİLTRESİ
        rel_perf_1m = perf_1m - market_perf_median
        is_falling_knife = False
        
        if rel_perf_1m < max_rel_knife:
            is_falling_knife = True
        elif close <= low_1m * 1.004:
            is_falling_knife = True
        elif perf_1m > 40.0 or perf_w > 20.0:
            is_falling_knife = True

        # C. ZOMBİ ŞİRKET KALKANI (Zarardaki şirketler swing lideri olamaz)
        is_zombie_company = (roe < min_roe_floor) or (pb <= 0.0)

        # 1. Taban Akümülasyon Skoru
        score_accum = 20.0
        if (dist_from_bottom >= min_dist) and (range_pos <= max_range_pos):
            score_accum = 85.0
            if rvol >= 1.25:
                score_accum = 100.0
        elif range_pos <= 55.0 and dist_from_bottom <= 16.0:
            score_accum = 50.0

        # 2. Temel Kalite Skoru
        if is_zombie_company:
            score_fund = 10.0
        else:
            score_fund = 40.0
            if roe >= 25.0: score_fund += 35.0
            elif roe >= 15.0: score_fund += 20.0

            if 0.6 <= pb <= 3.5: score_fund += 25.0
            elif pb <= 6.0: score_fund += 10.0

        score_fund = min(max(score_fund, 5.0), 100.0)

        # 3. Kurumsal Süpürme & Kapanış Gücü
        range_span = high - low
        clv = ((close - low) - (high - close)) / range_span if range_span > 0 else 0.0
        score_sweep = (f_ratio * 0.40) + (max(clv, 0.0) * 60.0)
        score_sweep = round(min(max(score_sweep, 5.0), 98.5), 1)

        # 4. Hacim & Uyanış Skoru
        vol_z = round(min(max(float((rvol - 1.0) * 1.85), -2.0), 5.0), 2)
        score_vol = round(min(max((rvol * 35.0) + (max(change, 0.0) * 5.0), 10.0), 100.0), 1)

        item['range_position'] = round(range_pos, 1)
        item['dist_from_bottom'] = round(dist_from_bottom, 1)
        item['stop_price'] = stop_price
        item['target_price'] = target_price
        item['rel_perf_1m'] = round(rel_perf_1m, 1)
        item['score_accum'] = score_accum
        item['score_fund'] = score_fund
        item['score_sweep'] = score_sweep
        item['score_vol'] = score_vol
        item['vol_z'] = vol_z
        item['is_falling_knife'] = is_falling_knife
        item['is_zombie'] = is_zombie_company
        scored_data.append(item)

    res_df = pd.DataFrame(scored_data)
    if res_df.empty:
        return res_df

    # Yüzdelik Normalizasyon
    res_df['pct_accum'] = res_df['score_accum'].rank(pct=True) * 100.0
    res_df['pct_fund'] = res_df['score_fund'].rank(pct=True) * 100.0
    res_df['pct_sweep'] = res_df['score_sweep'].rank(pct=True) * 100.0
    res_df['pct_vol'] = res_df['score_vol'].rank(pct=True) * 100.0

    w_a = weights.get('accum', 0.35)
    w_f = weights.get('fundamental', 0.30)
    w_s = weights.get('sweep', 0.20)
    w_v = weights.get('vol_mom', 0.15)

    raw_score = np.round(
        res_df['pct_accum'] * w_a +
        res_df['pct_fund'] * w_f +
        res_df['pct_sweep'] * w_s +
        res_df['pct_vol'] * w_v,
        1
    )

    # İnfaz: Zombi şirketler, düşen bıçaklar ve eksi kapatanlar elenir
    res_df['quant_score'] = np.where(
        (res_df['change_%'] > 0.0) & (~res_df['is_falling_knife']) & (~res_df['is_zombie']),
        raw_score,
        0.0
    )

    conditions = [
        res_df['is_zombie'] & (res_df['change_%'] > 4.0),
        res_df['is_falling_knife'],
        (res_df['quant_score'] >= 65.0) & (res_df['range_position'] <= max_range_pos),
        (res_df['quant_score'] >= 50.0),
        (res_df['change_%'] < -1.5)
    ]
    choices = [
        "⚠️ SPEKÜLATİF (ZARARDA ŞİRKET)",
        "🪤 DÜŞEN BIÇAK (RÖLATİF ÇÖKÜŞ)",
        "🎯 TABANDAN DÖNÜŞ (SWING LİDERİ)",
        "⚡ TABANDA SIKIŞMA (TAKİP)",
        "🚨 KURUMSAL BOŞALTIM"
    ]
    res_df['regime'] = np.select(conditions, choices, default="NÖTR")

    drop_cols = ['pct_accum', 'pct_fund', 'pct_sweep', 'pct_vol', 'is_falling_knife', 'is_zombie']
    res_df = res_df.drop(columns=[col for col in drop_cols if col in res_df.columns])

    res_df['score_diff'] = 0.0
    if not df_gecmis.empty and 'quant_score' in df_gecmis.columns:
        son_tarih = df_gecmis['tarih'].max()
        df_son = df_gecmis[df_gecmis['tarih'] == son_tarih]
        eski_map = dict(zip(df_son['ticker'], df_son['quant_score']))
        res_df['score_diff'] = np.round(res_df['quant_score'] - res_df['ticker'].map(eski_map).fillna(res_df['quant_score']), 1)

    return res_df.sort_values(by='quant_score', ascending=False).reset_index(drop=True)

# =============================================================================
# 3. SİNYAL YAŞAM DÖNGÜSÜ GÜNLÜĞÜ
# =============================================================================

def log_lifecycle_signals(df_scored, state):
    try:
        valid = df_scored[(df_scored['quant_score'] > 0.0) & (df_scored['regime'].str.contains("SWING LİDERİ"))]
        if valid.empty:
            return

        cutoff = float(np.percentile(valid['quant_score'], 60.0))
        leaders = valid[valid['quant_score'] >= max(cutoff, 65.0)].head(8)
        if leaders.empty:
            return

        today = pd.Timestamp.now().normalize()
        history_df = load_lifecycle_signals()

        new_entries = []
        for _, row in leaders.iterrows():
            new_entries.append({
                "tarih": today,
                "ticker": row["ticker"],
                "entry_price": float(row["close"]),
                "stop_price": float(row.get("stop_price", row["close"] * 0.90)),
                "target_price": float(row.get("target_price", row["close"] * 1.25)),
                "quant_score": float(row["quant_score"]),
                "regime": row["regime"],
                "score_accum": float(row.get("score_accum", 50.0)),
                "score_fund": float(row.get("score_fund", 50.0)),
                "score_sweep": float(row.get("score_sweep", 50.0)),
                "score_vol": float(row.get("score_vol", 50.0)),
                "ret_15d": np.nan,
                "ret_30d": np.nan,
                "ret_60d": np.nan,
                "max_drawdown": 0.0,
                "peak_gain": 0.0,
                "outcome": "PENDING"
            })

        df_new = pd.DataFrame(new_entries)
        if not history_df.empty:
            history_df["tarih"] = pd.to_datetime(history_df["tarih"])
            history_df = history_df[history_df["tarih"] != today]
            updated_history = pd.concat([history_df, df_new], ignore_index=True)
        else:
            updated_history = df_new

        updated_history.to_csv(LIFECYCLE_LOG_FILE, index=False)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {len(df_new)} adet teyitli dinamik sinyal deftere kaydedildi.")
    except Exception as e:
        print(f"⚠️ Yaşam döngüsü kayıt hatası: {e}")

# =============================================================================
# 4. TELEGRAM BİLDİRİMİ (HTML GÜVENLİ & OTOMATİK KURTARMA)
# =============================================================================

def send_telegram(message):
    token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = os.environ.get('CHAT_ID')
    if not token or not chat_id:
        print("⚠️ Telegram token veya CHAT_ID bulunamadı!")
        return
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    try:
        # 1. HTML Modunda Gönderim
        res = requests.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10
        )
        
        # HTML reddedilirse anında düz metin fallback'e geç
        if res.status_code != 200:
            print(f"⚠️ Telegram HTML parse uyarısı (HTTP {res.status_code}): {res.text}")
            print("🔄 Düz metin olarak tekrar gönderiliyor...")
            
            plain_text = (
                message.replace("<b>", "").replace("</b>", "")
                       .replace("<i>", "").replace("</i>", "")
                       .replace("<code>", "").replace("</code>", "")
            )
            res_plain = requests.post(
                url,
                json={"chat_id": chat_id, "text": plain_text},
                timeout=10
            )
            if res_plain.status_code == 200:
                print("✅ Telegram mesajı düz metin olarak başarıyla iletildi.")
            else:
                print(f"❌ Telegram tamamen başarısız: {res_plain.text}")
        else:
            print("✅ Telegram raporu HTML olarak başarıyla iletildi.")
            
    except Exception as e:
        print(f"⚠️ Telegram Bağlantı Hatası: {e}")

def format_telegram_report(df_scored, state):
    leaders = df_scored[df_scored['regime'].str.contains("SWING LİDERİ")].head(6)
    audit = state.get("audit_summary", {})
    weights = state.get("weights", {})
    
    msg = "🎯 <b>BIST ADAPTİF TABAN VE SWING MOTORU</b>\n"
    msg += f"🗓 <i>{datetime.now().strftime('%Y-%m-%d')} | Kapanış Raporu</i>\n"
    msg += f"🤖 <b>AI Durumu:</b> <code>{audit.get('status', 'AKTİF')}</code>\n"
    msg += f"⚖️ <b>Dinamik Ağırlık:</b> Taban: %{int(weights.get('accum', 0)*100)} | Temel: %{int(weights.get('fundamental', 0)*100)} | Takas: %{int(weights.get('sweep', 0)*100)} | Hacim: %{int(weights.get('vol_mom', 0)*100)}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

    if leaders.empty:
        msg += "ℹ️ <i>Bugün dinamik taban kriterlerine uyan, zararda olmayan kaliteli hisse tespit edilemedi.</i>\n\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += "🛡️ <i>Zombi Kalkanı: Zarardaki şirketler (ROE %5 altı) ve piyasadan negatif ayrışan düşen bıçaklar elenmiştir.</i>"
        return msg

    for idx, row in leaders.iterrows():
        s_diff = row.get('score_diff', 0.0)
        fark_str = f"+{s_diff:.1f}" if s_diff > 0 else f"{s_diff:.1f}"
        
        msg += f"⭐ <b>#{row['ticker']}</b> ── <b>Skor: {row['quant_score']:.1f}</b> <i>({fark_str})</i>\n"
        msg += f"💵 Fiyat: <b>{row['close']:.2f} TL</b> (<b>%{row['change_%']:+.2f}</b>)\n"
        msg += f"📍 Taban Konumu: <b>Kanalın %{row['range_position']:.0f}'i</b> (Dipten: %{row['dist_from_bottom']:+.1f})\n"
        msg += f"🛡️ Dinamik Stop: <b>{row.get('stop_price', 0):.2f} TL</b> | Hedef: <b>{row.get('target_price', 0):.2f} TL</b>\n"
        msg += f"💎 Temel: <b>{row['score_fund']:.0f}/100</b> (ROE: %{row.get('roe', 0):.1f}) | Takas: <b>%{row['score_sweep']:.1f}</b>\n"
        msg += f"🏷 Durum: <code>{row['regime']}</code>\n\n"

    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += "🛡️ <i>Zombi Kalkanı: Zarardaki şirketler (ROE %5 altı) ve piyasadan negatif ayrışan düşen bıçaklar elenmiştir.</i>"
    return msg

# =============================================================================
# 5. ANA YÜRÜTÜCÜ
# =============================================================================

def main():
    print("=== BIST Adaptif Akümülasyon Motoru Başlıyor ===")
    
    # 1. Denetçi
    state = audit_and_calibrate()

    # 2. Piyasa Verisi
    df_current = fetch_all_market_data()
    if df_current.empty:
        print("❌ Piyasa verisi alınamadı.")
        return

    # 3. Geçmiş Veri
    df_gecmis = pd.DataFrame()
    if os.path.exists(GECMIS_DOSYA):
        try:
            df_gecmis = pd.read_csv(GECMIS_DOSYA)
            df_gecmis['tarih'] = pd.to_datetime(df_gecmis['tarih'])
        except Exception:
            pass

    # 4. Dinamik Skorlama
    df_scored = calculate_quant_scores(df_current, df_gecmis, state)
    if df_scored.empty:
        return

    # 5. Yaşam Döngüsüne Kaydet
    log_lifecycle_signals(df_scored, state)

    # 6. Streamlit Arşivi
    if not df_gecmis.empty:
        df_gecmis = df_gecmis[df_gecmis['tarih'] != pd.Timestamp.now().normalize()]
        df_yeni = pd.concat([df_gecmis, df_scored], ignore_index=True)
    else:
        df_yeni = df_scored

    df_yeni['tarih'] = pd.to_datetime(df_yeni['tarih'])
    limit_tarih = pd.Timestamp.now().normalize() - pd.Timedelta(days=35)
    df_yeni[df_yeni['tarih'] >= limit_tarih].to_csv(GECMIS_DOSYA, index=False)

    # 7. Telegram Raporu
    send_telegram(format_telegram_report(df_scored, state))
    print("İşlem Başarıyla Tamamlandı!")

if __name__ == "__main__":
    main()
