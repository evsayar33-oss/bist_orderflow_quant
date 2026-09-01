import streamlit as st
import pandas as pd
import numpy as np
import os

st.set_page_config(page_title="BIST Fresh Breakout Terminal", layout="wide", page_icon="🏛️")

st.title("🏛️ BIST Taze Taban Kırılım & Liderlik Terminali")
st.markdown("*Haftalar öncesinden yükselmiş treni kaçan hisseleri eleyen; **20 günlük sıkışma tabanından YENİ KOPAN (Day 1-2)** taze hisseleri bulan Quant Motoru.*")

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

df_gecmis = load_data()

if not df_gecmis.empty:
    son_tarih = df_gecmis['tarih'].max()
    df = df_gecmis[df_gecmis['tarih'] == son_tarih].copy()
    
    st.caption(f"🗓️ Son Tarama: **{son_tarih.strftime('%Y-%m-%d')}** | 📊 Taranan Hisse: **{len(df)}**")

    required_cols = ['quant_score', 'score_diff', 'pivot_proximity', 'distance_from_base', 'sweep_ratio', 'vol_z', 'perf_w', 'perf_1m', 'change_%', 'close']
    for c in required_cols:
        if c not in df.columns:
            df[c] = 0.0
        else:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).round(2)

    if 'regime' not in df.columns:
        df['regime'] = 'NÖTR'

    # SIDEBAR
    st.sidebar.header("🔍 Hisse Kırılım Sorgu")
    search_ticker = st.sidebar.text_input("Hisse Kodu (Örn: THYAO):").upper()
    
    if search_ticker:
        h_data = df[df['ticker'] == search_ticker]
        if not h_data.empty:
            score = float(h_data['quant_score'].iloc[0])
            diff = float(h_data['score_diff'].iloc[0])
            regime = h_data['regime'].iloc[0]
            dist_base = float(h_data['distance_from_base'].iloc[0])
            p_prox = float(h_data['pivot_proximity'].iloc[0])
            sweep = float(h_data['sweep_ratio'].iloc[0])
            
            st.sidebar.metric(f"{search_ticker} Kırılım Skoru", f"{score:.1f}", f"{diff:+.1f}")
            st.sidebar.write(f"**Durum:** {regime}")
            st.sidebar.write(f"**Tabandan Uzaklık:** %{dist_base:.1f}")
            st.sidebar.write(f"**20G Zirve Yakınlığı:** %{p_prox:.1f}")
            st.sidebar.write(f"**Kurumsal Süpürme:** %{sweep:.1f}")
            
            st.sidebar.write("📈 Son 30 Günlük Skor:")
            trend = df_gecmis[df_gecmis['ticker'] == search_ticker][['tarih', 'quant_score']].sort_values('tarih')
            if not trend.empty:
                trend.set_index('tarih', inplace=True)
                st.sidebar.line_chart(trend['quant_score'])
        else:
            st.sidebar.warning("Hisse bulunamadı.")

    # 1. ANA TABLO: TAZE TABAN KIRILIMLARI (TOP 20)
    st.subheader("🚀 Taze Taban Kırılım Liderleri (Top 20)")
    st.markdown("*20 günlük zirvesini yeni kıran, tabanına yakın (%0-%15) taze Day 1-2 hisseleri.*")
    
    top_candidates = df[df['quant_score'] > 0.0].sort_values(by='quant_score', ascending=False).head(20)
    
    display_cols = ['ticker', 'quant_score', 'score_diff', 'regime', 'distance_from_base', 'pivot_proximity', 'vol_z', 'sweep_ratio', 'perf_w', 'change_%', 'close']
    display_cols = [c for c in display_cols if c in df.columns]
    
    col_names = {
        'ticker': 'Hisse',
        'quant_score': 'Kırılım Skoru',
        'score_diff': 'İvme Farkı',
        'regime': 'Kırılım Durumu',
        'distance_from_base': 'Tabandan Uzaklık %',
        'pivot_proximity': '20G Zirve %',
        'vol_z': 'Hacim Z',
        'sweep_ratio': 'Süpürme %',
        'perf_w': '1H %',
        'change_%': 'Günlük %',
        'close': 'Fiyat (TL)'
    }
    
    if not top_candidates.empty:
        st.dataframe(
            top_candidates[display_cols].rename(columns=col_names),
            column_config={
                "Kırılım Skoru": st.column_config.ProgressColumn("Kırılım Skoru", min_value=0, max_value=100, format="%.1f"),
                "Tabandan Uzaklık %": st.column_config.NumberColumn("Tabandan Uzaklık %", format="%+0.1f%%"),
                "20G Zirve %": st.column_config.NumberColumn("20G Zirve %", format="%0.1f%%"),
                "Hacim Z": st.column_config.NumberColumn("Hacim Z", format="%+.2fσ"),
                "Süpürme %": st.column_config.NumberColumn("Süpürme %", format="%%%0.1f"),
                "1H %": st.column_config.NumberColumn("1H %", format="%+0.1f%%"),
                "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+0.2f%%"),
                "Fiyat (TL)": st.column_config.NumberColumn("Fiyat (TL)", format="%.2f TL"),
                "İvme Farkı": st.column_config.NumberColumn("İvme Farkı", format="%+0.1f")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("ℹ️ Bugün kriterleri karşılayan taze bir taban kırılımı bulunamadı.")

    st.divider()

    # 2. DİSKALİFİYE EDİLENLER: TRENİ KAÇANLAR
    st.subheader("🚫 Treni Kaçanlar (Tabandan Aşırı Uzaklaşmış - Uzak Dur)")
    st.markdown("*Tabanından %35'ten fazla uzaklaşmış, tepede tükeniş yaşayan hisseler.*")
    
    traps = df[df['regime'].str.contains('TREN KAÇTI|DUMP', na=False)].sort_values(by='distance_from_base', ascending=False).head(15)
    if not traps.empty:
        st.dataframe(
            traps[display_cols].rename(columns=col_names),
            column_config={
                "Kırılım Skoru": st.column_config.NumberColumn("Kırılım Skoru", format="%.1f"),
                "Tabandan Uzaklık %": st.column_config.NumberColumn("Tabandan Uzaklık %", format="%+0.1f%%"),
                "20G Zirve %": st.column_config.NumberColumn("20G Zirve %", format="%0.1f%%"),
                "Hacim Z": st.column_config.NumberColumn("Hacim Z", format="%+.2fσ"),
                "Süpürme %": st.column_config.NumberColumn("Süpürme %", format="%%%0.1f"),
                "1H %": st.column_config.NumberColumn("1H %", format="%+0.1f%%"),
                "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+0.2f%%"),
                "Fiyat (TL)": st.column_config.NumberColumn("Fiyat (TL)", format="%.2f TL"),
                "İvme Farkı": st.column_config.NumberColumn("İvme Farkı", format="%+0.1f")
            },
            use_container_width=True,
            hide_index=True
        )

else:
    st.info("🕒 Sistem başlatılıyor... Lütfen GitHub Actions üzerinden 'Run workflow' yapınız.")
