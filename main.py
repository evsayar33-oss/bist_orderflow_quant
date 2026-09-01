import pandas as pd
import numpy as np
import os
import requests
import json
from datetime import datetime

from flow_fetcher import fetch_all_data
from flow_engine import calculate_quant_scores, gecmis_veriyi_yukle, GECMIS_DOSYA

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

def format_quant_report(df_scored):
    # En yüksek puanlı ilk 10 taze kırılım lideri
    leaders = df_scored[df_scored['quant_score'] >= 60.0].head(10)
    
    msg = "🏛️ <b>BIST TAZE TABAN KIRILIM RAPORU (DAY 1-2)</b>\n"
    msg += f"🗓 <i>{datetime.now().strftime('%Y-%m-%d')} | Saat: 17:00 Kapanış</i>\n"
    msg += "<i>(Haftalar önce yükselenler elenmiş, tabandan YENİ KOPANLAR seçilmiştir)</i>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if leaders.empty:
        msg += "ℹ️ <i>Bugün yeni bir taban kırılımı tespit edilemedi.</i>"
        return msg

    for idx, row in leaders.iterrows():
        s_diff = row.get('score_diff', 0)
        fark_str = f"+{s_diff:.1f}" if s_diff > 0 else f"{s_diff:.1f}"
        
        msg += f"🚀 <b>#{row['ticker']}</b> ── <b>[Skor: {row['quant_score']:.1f}]</b> <i>({fark_str})</i>\n"
        msg += f"💵 Fiyat: <b>{row['close']:.2f} TL</b> (<b>%{row['change_%']:+.2f}</b>)\n"
        msg += f"🎯 Tabandan Uzaklık: <b>%{row['distance_from_base']:.1f}</b> (Taze Kırılım!)\n"
        msg += f"📊 Hacim Z: <b>+{row['vol_z']:.1f}σ</b> | Süpürme: <b>%{row['sweep_ratio']:.1f}</b>\n"
        msg += f"🏷 Durum: <code>{row['regime']}</code>\n\n"
        
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += "⚡ <i>Strateji: 20 Günlük Sıkışmadan Yeni Kalkan Liderler</i>"
    
    return msg

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] === Fresh Breakout Scanner Başlıyor ===")
    
    df_current = fetch_all_data()
    if df_current.empty:
        print("Hata: Piyasa verisi alınamadı.")
        return

    df_gecmis = gecmis_veriyi_yukle()
    df_scored = calculate_quant_scores(df_current, df_gecmis)
    
    if df_scored.empty:
        print("Puanlanmış veri boş döndü.")
        return

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
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Başarılı! {GECMIS_DOSYA} kaydedildi.")

    telegram_msg = format_quant_report(df_scored)
    send_telegram_message(telegram_msg)

if __name__ == "__main__":
    main()
