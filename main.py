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
    sweepers = df_scored[df_scored['quant_score'] >= 60.0].head(10)
    
    msg = f"🏛️ <b>BIST ÖZ-ÖĞRENEN QUANT LİDERLER RAPORU</b>\n"
    msg += f"🗓 <i>Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>\n"
    msg += f"🤖 <i>Model Durumu: {ai_status}</i>\n"
    msg += f"⚖️ <i>AI Ağırlıkları: Trend %{int(weights['persistence']*100)} | Bilanço %{int(weights['growth']*100)} | Süpürme %{int(weights['sweep']*100)} | Hacim %{int(weights['vol_z']*100)}</i>\n\n"
    
    if sweepers.empty:
        msg += "⚠️ <i>Bugün kriterlere uyan lider hisse bulunamadı.</i>"
        return msg

    msg += "🚀 <b>GÜNÜN QUANT LİDERLERİ (Top 10)</b>\n"
    for idx, row in sweepers.iterrows():
        fark = f"(+{row['score_diff']:.1f})" if row.get('score_diff', 0) > 0 else f"({row.get('score_diff', 0):.1f})"
        msg += f"• <b>{row['ticker']}</b> : Skor: <b>{row['quant_score']:.1f}</b> {fark} | Fiyat: {row['close']} TL\n"
        msg += f"  └ <i>Süpürme: %{row['sweep_ratio']:.1f} | 3A Trend: %{row['perf_3m']:+.1f} | {row['regime']}</i>\n"
        
    return msg

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] === Self-Learning Quant Terminal Başlıyor ===")
    
    # 1. Canlı Veri Çekimi
    df_current = fetch_all_data()
    if df_current.empty:
        print("Hata: Piyasa verisi alınamadı.")
        return

    # 2. YAPAY ZEKA GERİ BESLEME VE ÖĞRENME ADIMI
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Geçmiş sinyallerin gerçek getirileri kontrol ediliyor...")
    update_realized_returns(df_current)
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Faktör ağırlıkları Walk-Forward ile kalibre ediliyor...")
    dynamic_weights, ai_status = calibrate_dynamic_weights()
    
    # Ağırlıkları Streamlit'in okuması için kaydet
    with open(WEIGHTS_FILE, 'w') as f:
        json.dump({"weights": dynamic_weights, "status": ai_status}, f)

    # 3. Dinamik Ağırlıklarla Puanlama
    df_gecmis = gecmis_veriyi_yukle()
    df_scored = calculate_quant_scores(df_current, df_gecmis, dynamic_weights)
    
    if df_scored.empty:
        print("Puanlanmış veri boş döndü.")
        return

    # 4. Bugünün Liderlerini Gelecekte Getirisini Ölçmek Üzere Kaydet
    log_today_signals(df_scored)

    # 5. Veritabanını Güncelle
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

    # 6. Telegram Raporu
    telegram_msg = format_quant_report(df_scored, dynamic_weights, ai_status)
    send_telegram_message(telegram_msg)

if __name__ == "__main__":
    main()
