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

    # 1. ANA TABLO: KURUMSAL SÜPÜRME RADARI
    st.subheader("🚀 Kurumsal Süpürme & Likidite Boşluğu Liderleri")
    st.markdown("*Büyük kurumların agresif alım yaptığı (Sweep > %50) ve satıcı kademelerinin boşaldığı hisseler.*")
    
    top_candidates = df[df['quant_score'] >= 45.0].sort_values(by='quant_score', ascending=False)
    
    display_cols = ['ticker', 'quant_score', 'score_diff', 'regime', 'sweep_ratio', 'vol_z', 'kyle_lambda', 'value_traded', 'change_%']
    display_cols = [c for c in display_cols if c in df.columns]
    
    col_names = {
        'ticker': 'Hisse',
        'quant_score': 'Akış Skoru',
        'score_diff': 'İvme Farkı',
        'regime': 'Mikroyapı Rejimi',
        'sweep_ratio': 'Süpürme Oranı %',
        'vol_z': 'Hacim Sapması (Z)',
        'kyle_lambda': "Kyle's Lambda",
        'value_traded': 'İşlem Hacmi (TL)',
        'change_%': 'Günlük %'
    }
    
    if not top_candidates.empty:
        st.dataframe(
            top_candidates[display_cols].rename(columns=col_names).style.background_gradient(subset=['Akış Skoru'], cmap='Greens').format({
                'Akış Skoru': '{:.1f}',
                'İvme Farkı': '{:+.1f}',
                'Süpürme Oranı %': '%{:.1f}',
                'Hacim Sapması (Z)': '{:+.2f}σ',
                "Kyle's Lambda": '{:.2f}',
                'İşlem Hacmi (TL)': '{:,.0f}',
                'Günlük %': '%{:.2f}'
            }),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("ℹ️ Bugün net bir kurumsal süpürme gerçekleşmedi.")

    st.divider()

    # 2. DİSKALİFİYE EDİLENLER: KURUMSAL BOŞALTIM
    st.subheader("🚨 Kurumsal Boşaltım Radarı (Satış Baskısı)")
    st.markdown("*Yüksek hacimli satıcı baskısı veya kurumsal çıkış yiyen hisseler.*")
    
    dumps = df[df['regime'].str.contains('BOŞALTIM', na=False)].sort_values(by='vol_z', ascending=False).head(10)
    if not dumps.empty:
        st.dataframe(
            dumps[display_cols].rename(columns=col_names).style.background_gradient(subset=['Günlük %'], cmap='Reds_r').format({
                'Akış Skoru': '{:.1f}',
                'İvme Farkı': '{:+.1f}',
                'Süpürme Oranı %': '%{:.1f}',
                'Hacim Sapması (Z)': '{:+.2f}σ',
                "Kyle's Lambda": '{:.2f}',
                'İşlem Hacmi (TL)': '{:,.0f}',
                'Günlük %': '%{:.2f}'
            }),
            use_container_width=True,
            hide_index=True
        )

else:
    st.info("🕒 Sistem başlatılıyor... Lütfen GitHub Actions üzerinden 'Run workflow' yapınız.")
