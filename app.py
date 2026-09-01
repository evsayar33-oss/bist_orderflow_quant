import streamlit as st
import pandas as pd
import numpy as np
import os
import json

st.set_page_config(page_title="BIST Medium-Term Leaders Terminal", layout="wide", page_icon="🏛️")

st.title("🏛️ BIST Orta Vadeli Liderler Terminali")
st.markdown("*Son 1-2 haftada aşırı primlenmiş veya o gün eksi kapatan hisselerden arındırılmış; sadece **75+ Genel Puanı ve Pozitif İvmesi olan** gerçek liderler.*")

WEIGHTS_FILE = "model_weights.json"

def load_ai_metadata():
    if os.path.exists(WEIGHTS_FILE):
        try:
            with open(WEIGHTS_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "weights": {"persistence": 0.40, "growth": 0.25, "sweep": 0.25, "vol_z": 0.10},
        "status": "🕒 BAZ AĞIRLIKLAR AKTİF"
    }

def load_data():
    if os.path.exists("gecmis_veri.csv"):
        try:
            df = pd.read_csv("gecmis_veri.csv")
            if 'tarih' in df.columns:
                df['tarih'] = pd.to_datetime(df['tarih'])
            return df
        except:
            return pd.DataFrame()
    return pd.DataFrame()

ai_meta = load_ai_metadata()
df_gecmis = load_data()

# ÜST AI PANELİ
w = ai_meta['weights']
st.info(f"🧠 **Model Durumu:** {ai_meta['status']}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("📈 Trend Sürekliliği Ağırlığı", f"%{int(w['persistence']*100)}")
c2.metric("💎 Bilanço Büyüme Ağırlığı", f"%{int(w['growth']*100)}")
c3.metric("🏛️ Kurumsal Süpürme Ağırlığı", f"%{int(w['sweep']*100)}")
c4.metric("⚡ Hacim Şoku Ağırlığı", f"%{int(w['vol_z']*100)}")

st.divider()

if not df_gecmis.empty:
    son_tarih = df_gecmis['tarih'].max()
    df = df_gecmis[df_gecmis['tarih'] == son_tarih].copy()
    
    st.caption(f"🗓️ Son Tarama: **{son_tarih.strftime('%Y-%m-%d')}** | 📊 Taranan Hisse: **{len(df)}**")

    required_cols = ['quant_score', 'score_diff', 'roe', 'growth_discount', 'sweep_ratio', 'perf_w', 'perf_1m', 'perf_3m', 'perf_6m', 'change_%']
    for c in required_cols:
        if c not in df.columns:
            df[c] = 0.0
        else:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).round(2)

    if 'regime' not in df.columns:
        df['regime'] = 'NÖTR'

    # SIDEBAR
    st.sidebar.header("🔍 Hisse Liderlik Sorgu")
    search_ticker = st.sidebar.text_input("Hisse Kodu (Örn: THYAO):").upper()
    
    if search_ticker:
        h_data = df[df['ticker'] == search_ticker]
        if not h_data.empty:
            score = float(h_data['quant_score'].iloc[0])
            diff = float(h_data['score_diff'].iloc[0])
            regime = h_data['regime'].iloc[0]
            roe_val = float(h_data['roe'].iloc[0])
            pw = float(h_data['perf_w'].iloc[0])
            p3m = float(h_data['perf_3m'].iloc[0])
            p6m = float(h_data['perf_6m'].iloc[0])
            sweep = float(h_data['sweep_ratio'].iloc[0])
            
            st.sidebar.metric(f"{search_ticker} Liderlik Skoru", f"{score:.1f}", f"{diff:+.1f}")
            st.sidebar.write(f"**Durum:** {regime}")
            st.sidebar.write(f"**Özsermaye Karlılığı (ROE):** %{roe_val:.1f}")
            st.sidebar.write(f"**1 Haftalık Prim:** %{pw:+.1f}")
            st.sidebar.write(f"**3A / 6A Getiri:** %{p3m:+.1f} / %{p6m:+.1f}")
            st.sidebar.write(f"**Kurumsal Süpürme:** %{sweep:.1f}")
            
            st.sidebar.write("📈 Son 30 Günlük Skor:")
            trend = df_gecmis[df_gecmis['ticker'] == search_ticker][['tarih', 'quant_score']].sort_values('tarih')
            if not trend.empty:
                trend.set_index('tarih', inplace=True)
                st.sidebar.line_chart(trend['quant_score'])
        else:
            st.sidebar.warning("Hisse bulunamadı.")

    # 1. ANA TABLO: SADECE 75+ VE POZİTİF OLANLAR
    st.subheader("🚀 Orta Vadeli Trend Liderleri (75+ Puan & Pozitif İvme)")
    st.markdown("*Son 1-2 haftada aşırı primlenmemiş, dinlenmeden yeni kalkan ve o gün mutlaka pozitif (+) olan liderler.*")
    
    top_candidates = df[
        (df['quant_score'] >= 75.0) & 
        (df['change_%'] > 0.0) & 
        (df['score_diff'] >= 0.0)
    ].sort_values(by='quant_score', ascending=False)
    
    display_cols = ['ticker', 'quant_score', 'score_diff', 'regime', 'roe', 'perf_w', 'perf_3m', 'perf_6m', 'sweep_ratio', 'change_%']
    display_cols = [c for c in display_cols if c in df.columns]
    
    col_names = {
        'ticker': 'Hisse',
        'quant_score': 'Liderlik Skoru',
        'score_diff': 'İvme Farkı',
        'regime': 'Liderlik Durumu',
        'roe': 'ROE (Karlılık %)',
        'perf_w': '1H Prim %',
        'perf_3m': '3A Trend %',
        'perf_6m': '6A Trend %',
        'sweep_ratio': 'Süpürme %',
        'change_%': 'Günlük %'
    }
    
    if not top_candidates.empty:
        st.dataframe(
            top_candidates[display_cols].rename(columns=col_names),
            column_config={
                "Liderlik Skoru": st.column_config.ProgressColumn("Liderlik Skoru", min_value=0, max_value=100, format="%.1f"),
                "ROE (Karlılık %)": st.column_config.NumberColumn("ROE (Karlılık %)", format="%%%0.1f"),
                "1H Prim %": st.column_config.NumberColumn("1H Prim %", format="%+0.1f%%"),
                "3A Trend %": st.column_config.NumberColumn("3A Trend %", format="%+0.1f%%"),
                "6A Trend %": st.column_config.NumberColumn("6A Trend %", format="%+0.1f%%"),
                "Süpürme %": st.column_config.NumberColumn("Süpürme %", format="%%%0.1f"),
                "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+0.2f%%"),
                "İvme Farkı": st.column_config.NumberColumn("İvme Farkı", format="%+0.1f")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("ℹ️ Bugün 75 puan ve üzeri kriteri karşılayan (pozitif) yeni bir orta vadeli lider bulunamadı.")

    st.divider()

    # 2. DİSKALİFİYE EDİLENLER: TRENİ KAÇANLAR & TUZAKLAR
    st.subheader("🚫 Diskalifiye Edilenler (Treni Kaçanlar & Tuzaklar - Uzak Dur)")
    st.markdown("*Son 1-2 haftada zaten aşırı primlenmiş (%18+) veya o gün eksi kapatan hisseler.*")
    
    traps = df[df['regime'].str.contains('TREN KAÇTI|DÜŞEN BIÇAK|EKSİ|ZOMBİ', na=False)].sort_values(by='perf_w', ascending=False).head(15)
    if not traps.empty:
        st.dataframe(
            traps[display_cols].rename(columns=col_names),
            column_config={
                "Liderlik Skoru": st.column_config.NumberColumn("Liderlik Skoru", format="%.1f"),
                "ROE (Karlılık %)": st.column_config.NumberColumn("ROE (Karlılık %)", format="%%%0.1f"),
                "1H Prim %": st.column_config.NumberColumn("1H Prim %", format="%+0.1f%%"),
                "3A Trend %": st.column_config.NumberColumn("3A Trend %", format="%+0.1f%%"),
                "6A Trend %": st.column_config.NumberColumn("6A Trend %", format="%+0.1f%%"),
                "Süpürme %": st.column_config.NumberColumn("Süpürme %", format="%%%0.1f"),
                "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+0.2f%%"),
                "İvme Farkı": st.column_config.NumberColumn("İvme Farkı", format="%+0.1f")
            },
            use_container_width=True,
            hide_index=True
        )

else:
    st.info("🕒 Sistem başlatılıyor... Lütfen GitHub Actions üzerinden 'Run workflow' yapınız.")
