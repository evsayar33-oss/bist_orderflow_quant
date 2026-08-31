import streamlit as st
import pandas as pd
import numpy as np
import os

st.set_page_config(page_title="BIST Institutional Order Flow Terminal", layout="wide", page_icon="🏛️")

st.title("🏛️ BIST Kurumsal Emir Akışı & Likidite Terminali")
st.markdown("*Ölü kedi tuzaklarından ve aşırı şişmiş tepelerden arındırılmış; saf Kurumsal Süpürme (Sweep) ve Kyle's Lambda quant motoru.*")

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

    format_cols = ['quant_score', 'score_diff', 'sweep_ratio', 'kyle_lambda', 'vol_z', 'value_traded', 'change_%', 'perf_3m']
    for col in format_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).round(2)

    # SIDEBAR
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
            p3m = float(h_data['perf_3m'].iloc[0])
            
            st.sidebar.metric(f"{search_ticker} Akış Skoru", f"{score:.1f}", f"{diff:+.1f}")
            st.sidebar.write(f"**Durum:** {regime}")
            st.sidebar.write(f"**Kurumsal Süpürme:** %{sweep:.1f}")
            st.sidebar.write(f"**3 Aylık Performans:** %{p3m:+.1f}")
            st.sidebar.write(f"**Kyle's Lambda:** {lambda_val:.2f}")
            st.sidebar.write(f"**Hacim Z-Skoru:** {vol_z:+.1f}σ")
            
            st.sidebar.write("📈 Son 30 Günlük Skor:")
            trend = df_gecmis[df_gecmis['ticker'] == search_ticker][['tarih', 'quant_score']].sort_values('tarih')
            if not trend.empty:
                trend.set_index('tarih', inplace=True)
                st.sidebar.line_chart(trend['quant_score'])
        else:
            st.sidebar.warning("Hisse bulunamadı.")

    # 1. ANA TABLO: GERÇEK SÜPÜRMELER
    st.subheader("🚀 Gerçek Kurumsal Süpürme Liderleri (Top 20)")
    st.markdown("*Düşüş trendinde olmayan, tabandan sağlıklı süpürülen gerçek liderler.*")
    
    top_candidates = df[df['quant_score'] > 0.0].sort_values(by='quant_score', ascending=False).head(20)
    
    display_cols = ['ticker', 'quant_score', 'score_diff', 'regime', 'sweep_ratio', 'vol_z', 'kyle_lambda', 'perf_3m', 'change_%']
    display_cols = [c for c in display_cols if c in df.columns]
    
    col_names = {
        'ticker': 'Hisse',
        'quant_score': 'Akış Skoru',
        'score_diff': 'İvme Farkı',
        'regime': 'Mikroyapı Rejimi',
        'sweep_ratio': 'Süpürme %',
        'vol_z': 'Hacim Z',
        'kyle_lambda': "Kyle's Lambda",
        'perf_3m': '3A Trend %',
        'change_%': 'Günlük %'
    }
    
    if not top_candidates.empty:
        st.dataframe(
            top_candidates[display_cols].rename(columns=col_names),
            column_config={
                "Akış Skoru": st.column_config.ProgressColumn("Akış Skoru", min_value=0, max_value=100, format="%.1f"),
                "Süpürme %": st.column_config.NumberColumn("Süpürme %", format="%.1f%%"),
                "Hacim Z": st.column_config.NumberColumn("Hacim Z", format="%+.2fσ"),
                "Kyle's Lambda": st.column_config.NumberColumn("Kyle's Lambda", format="%.2f"),
                "3A Trend %": st.column_config.NumberColumn("3A Trend %", format="%+.1f%%"),
                "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+.2f%%"),
                "İvme Farkı": st.column_config.NumberColumn("İvme Farkı", format="%+.1f")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("ℹ️ Bugün kriterleri karşılayan temiz bir kurumsal süpürme bulunamadı.")

    st.divider()

    # 2. DİSKALİFİYE EDİLENLER: TUZAKLAR VE BOŞALTIMLAR
    st.subheader("🪤 Ölü Kedi Tuzakları & Boşaltım Radarı (Uzak Dur)")
    st.markdown("*Düşüş trendinde tepki veren sahte yükselişler veya kurumsal mal çıkışları.*")
    
    traps = df[df['regime'].str.contains('TUZAĞI|BOŞALTIM|AŞIRI', na=False)].sort_values(by='perf_3m', ascending=True).head(15)
    if not traps.empty:
        st.dataframe(
            traps[display_cols].rename(columns=col_names),
            column_config={
                "Akış Skoru": st.column_config.NumberColumn("Akış Skoru", format="%.1f"),
                "Süpürme %": st.column_config.NumberColumn("Süpürme %", format="%.1f%%"),
                "Hacim Z": st.column_config.NumberColumn("Hacim Z", format="%+.2fσ"),
                "Kyle's Lambda": st.column_config.NumberColumn("Kyle's Lambda", format="%.2f"),
                "3A Trend %": st.column_config.NumberColumn("3A Trend %", format="%+.1f%%"),
                "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+.2f%%"),
                "İvme Farkı": st.column_config.NumberColumn("İvme Farkı", format="%+.1f")
            },
            use_container_width=True,
            hide_index=True
        )

else:
    st.info("🕒 Sistem başlatılıyor... Lütfen GitHub Actions üzerinden 'Run workflow' yapınız.")
