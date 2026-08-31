import pandas as pd
import numpy as np
import os
import requests
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
    sweepers = df_scored[df_scored['quant_score'] >= 60.0].head(10)
    
    msg = f"🏛️ <b>BIST GERÇEK KURUMSAL SÜPÜRME RAPORU</b>\n"
    msg += f"🗓 <i>Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>\n"
    msg += "<i>(Ölü kedi tuzakları elenmiş, gerçek trend süpürmeleri seçilmiştir)</i>\n\n"
    
    if sweepers.empty:
        msg += "⚠️ <i>Bugün kriterlere uyan net bir kurumsal süpürme bulunamadı.</i>"
        return msg

    msg += "🚀 <b>KURUMSAL SÜPÜRME LİDERLERİ (Top 10)</b>\n"
    for idx, row in sweepers.iterrows():
        fark = f"(+{row['score_diff']:.1f})" if row.get('score_diff', 0) > 0 else f"({row.get('score_diff', 0):.1f})"
        msg += f"• <b>{row['ticker']}</b> : Akış Skoru: <b>{row['quant_score']:.1f}</b> {fark} | Fiyat: %{row['change_%']:.1f}\n"
        msg += f"  └ <i>Süpürme: %{row['sweep_ratio']:.1f} | {row['regime']}</i>\n"
        
    return msg

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] === Trend Gated Order Flow Scanner Başlıyor ===")
    
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
