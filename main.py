import pandas as pd
import numpy as np
import os
import requests
import json
from datetime import datetime

from flow_fetcher import fetch_all_data
from flow_engine import calculate_quant_scores, gecmis_veriyi_yukle, GECMIS_DOSYA
from learner_engine import update_realized_returns, calibrate_dynamic_weights, log_today_signals, WEIGHTS_FILE

def send_telegram_message(message):
    token = os.environ.get('TELEGRAM_TOKEN')
    chat_id = os.environ.get('CHAT_ID')
    
    if not token or not chat_id:
        print("Telegram Token veya Chat ID bulunamadı.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram hatası: {e}")

def format_quant_report(df_scored, weights, ai_status):
    # KATI KURAL: Sadece 75+ Genel Puanı olan, o gün Pozitif (+) kapatan hisseler!
    leaders = df_scored[
        (df_scored['quant_score'] >= 75.0) & 
        (df_scored['change_%'] > 0.0) & 
        (df_scored['score_diff'] >= 0.0)
    ].sort_values(by='quant_score', ascending=False)
    
    msg = "🏛️ <b>BIST ORTA VADELİ LİDERLER LİSTESİ (75+ PUAN)</b>\n"
    msg += f"🗓 <i>{datetime.now().strftime('%Y-%m-%d')} | Saat: 17:00 Kapanış</i>\n"
    msg += "<i>(Eksi hisseler ve son 1-2 haftada aşırı primlenmiş olanlar elenmiştir)</i>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if leaders.empty:
        msg += "ℹ️ <i>Bugün 75 puan ve üzeri kriteri karşılayan (pozitif) yeni bir orta vadeli lider bulunamadı.</i>"
        return msg

    for idx, row in leaders.iterrows():
        s_diff = row.get('score_diff', 0)
        fark_str = f"+{s_diff:.1f}" if s_diff > 0 else f"{s_diff:.1f}"
        
        msg += f"🚀 <b>#{row['ticker']}</b> ── <b>[Skor: {row['quant_score']:.1f}]</b> <i>({fark_str})</i>\n"
        msg += f"💵 Fiyat: <b>{row['close']:.2f} TL</b>  (<b>%{row['change_%']:+.2f}</b>)\n"
        msg += f"📈 1 Haftalık / 3 Aylık: <b>%{row['perf_w']:+.1f}</b> / <b>%{row['perf_3m']:+.1f}</b>\n"
        msg += f"💎 Özsermaye Karlılığı (ROE): <b>%{row['roe']:.1f}</b>\n"
        msg += f"🏷 Durum: <code>{row['regime']}</code>\n\n"
        
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🎯 <i>Toplam {len(leaders)} adet temiz orta vadeli lider tespit edildi.</i>"
    
    return msg

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] === Self-Learning Quant Terminal Başlıyor ===")
    
    df_current = fetch_all_data()
    if df_current.empty:
        print("Hata: Piyasa verisi alınamadı.")
        return

    update_realized_returns(df_current)
    dynamic_weights, ai_status = calibrate_dynamic_weights()
    
    with open(WEIGHTS_FILE, 'w') as f:
        json.dump({"weights": dynamic_weights, "status": ai_status}, f)

    df_gecmis = gecmis_veriyi_yukle()
    df_scored = calculate_quant_scores(df_current, df_gecmis, dynamic_weights)
    
    if df_scored.empty:
        print("Puanlanmış veri boş döndü.")
        return

    log_today_signals(df_scored)

    if not df_gecmis.empty:
        bugun = pd.Timestamp.now().normalize()
        df_gecmis = df_gecmis[df_gecmis['tarih'] != bugun]
        df_yeni_gecmis = pd.concat([df_gecmis, df_scored], ignore_index=True)
    else:
        df_yeni_gecmis = df_scored

    df_yeni_gecmis['tarih'] = pd.to_datetime(df_yeni_gecmis['tarih'])
    limit_tarih = pd.Timestamp.now().normalize() - pd.Timedelta(days=30)
    df_yeni_gecmis = df_yeni_gecmis[df_yeni_gecmis['tarih'] >= limit_tarih]
    
    df_yeni_gecmis.to_csv(GECMIS_DOSYA, index=False)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Başarılı! {GECMIS_DOSYA} ve sinyaller güncellendi.")

    telegram_msg = format_quant_report(df_scored, dynamic_weights, ai_status)
    send_telegram_message(telegram_msg)

if __name__ == "__main__":
    main()
