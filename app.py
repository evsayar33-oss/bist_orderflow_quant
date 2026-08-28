import streamlit as st
import pandas as pd
import numpy as np
import os

st.set_page_config(page_title="BIST Institutional Order Flow Terminal", layout="wide", page_icon="🏛️")

st.title("🏛️ BIST Kurumsal Emir Akışı & Likidite Terminali")
st.markdown("*Teknik indikatörlerden arındırılmış; Kurumsal Süpürme (Sweep), Kyle's Lambda (Likidite Boşluğu) ve Emir Akışı Dengesizliği (OFI) quant motoru.*")

def load_data():
    if os.path.exists("gecmis_veri.csv"):
        try:
            df = pd.read_csv("gecmis_veri.csv")
            if 'tarih' in df.columns:
                df['tarih'] = pd.to_datetime(df['tarih'])
            return df
        except Exception as e:
            st.error(f"Dosya okuma hatası: {e}")
            return pd.DataFrame()
    return pd.DataFrame()

df_gecmis = load_data()

if not df_gecmis.empty:
    son_tarih = df_gecmis['tarih'].max()
    df = df_gecmis[df_gecmis['tarih'] == son_tarih].copy()
    
    st.caption(f"🗓️ Son Tarama: **{son_tarih.strftime('%Y-%m-%d')}** | 📊 Taranan Hisse: **{len(df)}**")

    format_cols = ['quant_score', 'score_diff', 'sweep_ratio', 'kyle_lambda', 'vol_z', 'value_traded', 'change_%']
    for col in format_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).round(2)

    # --- SIDEBAR: HİSSE AKIŞ SORGULAMA ---
    st.sidebar.header("🔍 Hisse Mikro-Yapı Sorgu")
    search_ticker = st.sidebar.text_input("Hisse Kodu (Örn: THYAO):").upper()
    
    if search_ticker:
        h_data = df[df['ticker'] == search_ticker]
        if not h_data.empty:
            score = float(h_data['quant_score'].iloc[0])
            diff = float(h_data['score_diff'].iloc[0])
            regime = h_data['regime'].iloc[0]
            sweep = float(h_data['sweep_ratio'].iloc[0])
            lambda_val = float(h_data['kyle_lambda'].iloc[0])
            vol_z = float(h_data['vol_z'].iloc[0])
            
            st.sidebar.metric(f"{search_ticker} Akış Skoru", f"{score:.1f}", f"{diff:+.1f}")
            st.sidebar.write(f"**Emir Akışı Rejimi:** {regime}")
            st.sidebar.write(f"**Kurumsal Süpürme:** %{sweep:.1f}")
            st.sidebar.write(f"**Kyle's Lambda (Fiyat Etkisi):** {lambda_val:.2f}")
            st.sidebar.write(f"**Artık Hacim Sapması:** {vol_z:+.1f}σ")
            
            st.sidebar.write("📈 Son 30 Günlük Akış Trendi:")
            trend = df_gecmis[df_gecmis['ticker'] == search_ticker][['tarih', 'quant_score']].sort_values('tarih')
            if not trend.empty:
                trend.set_index('tarih', inplace=True)
                st.sidebar.line_chart(trend['quant_score'])
        else:
            st.sidebar.warning("Hisse bulunamadı veya likidite barajına takıldı.")

    # --- 1. ANA TABLO: KURUMSAL SÜPÜRME RADARI (TOP 20) ---
    st.subheader("🚀 Kurumsal Süpürme & Likidite Boşluğu Liderleri (Top 20)")
    st.markdown("*Büyük kurumların agresif alım yaptığı (Sweep) ve satıcı kademelerinin boşaldığı hisseler.*")
    
    # En yüksek akış skoruna sahip ilk 20 hisseyi doğrudan göster
    top_candidates = df.sort_values(by='quant_score', ascending=False).head(20)
    
    display_cols = ['ticker', 'quant_score', 'score_diff', 'regime', 'sweep_ratio', 'vol_z', 'kyle_lambda', 'value_traded', 'change_%']
    display_cols = [c for c in display_cols if c in df.columns]
    
    col_names = {
        'ticker': 'Hisse',
        'quant_score': 'Akış Skoru',
        'score_diff': 'İvme Farkı',
        'regime': 'Mikroyapı Rejimi',
        'sweep_ratio': 'Süpürme %',
        'vol_z': 'Hacim Sapması (Z)',
        'kyle_lambda': "Kyle's Lambda",
        'value_traded': 'İşlem Hacmi (TL)',
        'change_%': 'Günlük %'
    }
    
    if not top_candidates.empty:
        df_show = top_candidates[display_cols].rename(columns=col_names)
        st.dataframe(
            df_show,
            column_config={
                "Akış Skoru": st.column_config.ProgressColumn("Akış Skoru", min_value=0, max_value=100, format="%.1f"),
                "Süpürme %": st.column_config.NumberColumn("Süpürme %", format="%.1f%%"),
                "Hacim Sapması (Z)": st.column_config.NumberColumn("Hacim Sapması (Z)", format="%+.2fσ"),
                "Kyle's Lambda": st.column_config.NumberColumn("Kyle's Lambda", format="%.2f"),
                "İşlem Hacmi (TL)": st.column_config.NumberColumn("İşlem Hacmi (TL)", format="%,.0f TL"),
                "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+.2f%%"),
                "İvme Farkı": st.column_config.NumberColumn("İvme Farkı", format="%+.1f")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("ℹ️ Veri bulunamadı.")

    st.divider()

    # --- 2. DİSKALİFİYE EDİLENLER: KURUMSAL BOŞALTIM ---
    st.subheader("🚨 Kurumsal Boşaltım Radarı (Satış Baskısı)")
    st.markdown("*Yüksek hacimli satıcı baskısı veya kurumsal çıkış yiyen hisseler.*")
    
    dumps = df[df['change_%'] < -1.0].sort_values(by='vol_z', ascending=False).head(10)
    if not dumps.empty:
        df_dump = dumps[display_cols].rename(columns=col_names)
        st.dataframe(
            df_dump,
            column_config={
                "Akış Skoru": st.column_config.NumberColumn("Akış Skoru", format="%.1f"),
                "Süpürme %": st.column_config.NumberColumn("Süpürme %", format="%.1f%%"),
                "Hacim Sapması (Z)": st.column_config.NumberColumn("Hacim Sapması (Z)", format="%+.2fσ"),
                "Kyle's Lambda": st.column_config.NumberColumn("Kyle's Lambda", format="%.2f"),
                "İşlem Hacmi (TL)": st.column_config.NumberColumn("İşlem Hacmi (TL)", format="%,.0f TL"),
                "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+.2f%%"),
                "İvme Farkı": st.column_config.NumberColumn("İvme Farkı", format="%+.1f")
            },
            use_container_width=True,
            hide_index=True
        )

else:
    st.info("🕒 Sistem başlatılıyor... Lütfen GitHub Actions üzerinden 'Run workflow' yapınız.")
