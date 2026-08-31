import streamlit as st
import pandas as pd
import numpy as np
import os

st.set_page_config(page_title="BIST Forward Quant & Compounder Terminal", layout="wide", page_icon="🏛️")

st.title("🏛️ BIST Gelecek Değerleme & Compounder Terminali")
st.markdown("*Geriye bakan SMA/EMA gibi çizgilerden arındırılmış; Gelecek Büyüme İskontosu (ROE/PB), Marj Genişlemesi ve Kurumsal Süpürme quant motoru.*")

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

    required_cols = ['quant_score', 'score_diff', 'pe', 'pb', 'roe', 'op_margin', 'sweep_ratio', 'growth_discount', 'change_%']
    for c in required_cols:
        if c not in df.columns:
            df[c] = 0.0
        else:
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).round(2)

    if 'regime' not in df.columns:
        df['regime'] = 'NÖTR'

    # SIDEBAR
    st.sidebar.header("🔍 Şirket Gelecek Değerleme Sorgu")
    search_ticker = st.sidebar.text_input("Hisse Kodu (Örn: THYAO):").upper()
    
    if search_ticker:
        h_data = df[df['ticker'] == search_ticker]
        if not h_data.empty:
            score = float(h_data['quant_score'].iloc[0])
            diff = float(h_data['score_diff'].iloc[0])
            regime = h_data['regime'].iloc[0]
            roe_val = float(h_data['roe'].iloc[0])
            pe_val = float(h_data['pe'].iloc[0])
            pb_val = float(h_data['pb'].iloc[0])
            op_m = float(h_data['op_margin'].iloc[0])
            sweep = float(h_data['sweep_ratio'].iloc[0])
            
            st.sidebar.metric(f"{search_ticker} Quant Skoru", f"{score:.1f}", f"{diff:+.1f}")
            st.sidebar.write(f"**Rejim:** {regime}")
            st.sidebar.write(f"**Özsermaye Karlılığı (ROE):** %{roe_val:.1f}")
            st.sidebar.write(f"**F/K:** {pe_val:.1f} | **PD/DD:** {pb_val:.2f}")
            st.sidebar.write(f"**Faaliyet Kar Marjı:** %{op_m:.1f}")
            st.sidebar.write(f"**Kurumsal Süpürme:** %{sweep:.1f}")
            
            st.sidebar.write("📈 Son 30 Günlük Skor:")
            trend = df_gecmis[df_gecmis['ticker'] == search_ticker][['tarih', 'quant_score']].sort_values('tarih')
            if not trend.empty:
                trend.set_index('tarih', inplace=True)
                st.sidebar.line_chart(trend['quant_score'])
        else:
            st.sidebar.warning("Hisse bulunamadı.")

    # 1. ANA TABLO: BİLEŞİK BÜYÜME ŞAMPİYONLARI
    st.subheader("🚀 Bileşik Büyüme & Gelecek Değerleme Liderleri (Top 20)")
    st.markdown("*Yüksek özsermaye karlılığına (ROE) sahip, geleceğe göre iskontolu ve kurumsal süpürmesi olan gerçek şirketler.*")
    
    top_candidates = df[df['quant_score'] > 0.0].sort_values(by='quant_score', ascending=False).head(20)
    
    display_cols = ['ticker', 'quant_score', 'score_diff', 'regime', 'roe', 'pe', 'pb', 'op_margin', 'sweep_ratio', 'change_%']
    display_cols = [c for c in display_cols if c in df.columns]
    
    col_names = {
        'ticker': 'Hisse',
        'quant_score': 'Gelecek Skoru',
        'score_diff': 'İvme Farkı',
        'regime': 'Kurumsal Rejim',
        'roe': 'Özsermaye Karlılığı (ROE)',
        'pe': 'F/K',
        'pb': 'PD/DD',
        'op_margin': 'Faaliyet Marjı %',
        'sweep_ratio': 'Süpürme %',
        'change_%': 'Günlük %'
    }
    
    if not top_candidates.empty:
        st.dataframe(
            top_candidates[display_cols].rename(columns=col_names),
            column_config={
                "Gelecek Skoru": st.column_config.ProgressColumn("Gelecek Skoru", min_value=0, max_value=100, format="%.1f"),
                "Özsermaye Karlılığı (ROE)": st.column_config.NumberColumn("Özsermaye Karlılığı (ROE)", format="%%%0.1f"),
                "F/K": st.column_config.NumberColumn("F/K", format="%.1f"),
                "PD/DD": st.column_config.NumberColumn("PD/DD", format="%.2f"),
                "Faaliyet Marjı %": st.column_config.NumberColumn("Faaliyet Marjı %", format="%%%0.1f"),
                "Süpürme %": st.column_config.NumberColumn("Süpürme %", format="%%%0.1f"),
                "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+0.2f%%"),
                "İvme Farkı": st.column_config.NumberColumn("İvme Farkı", format="%+0.1f")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("ℹ️ Bugün kriterleri karşılayan temiz bir şirket bulunamadı.")

    st.divider()

    # 2. DİSKALİFİYE EDİLENLER: ZOMBİ & ÇÖP ŞİRKETLER
    st.subheader("🚨 Zombi & İflas Riski Olan Şirketler (Uzak Dur)")
    st.markdown("*Zarar eden, özsermayesini eriten veya yüksek borç batağındaki çöp şirketler.*")
    
    traps = df[df['regime'].str.contains('ÇÖP|BOŞALTIM', na=False)].sort_values(by='roe', ascending=True).head(15)
    
    if not traps.empty:
        st.dataframe(
            traps[display_cols].rename(columns=col_names),
            column_config={
                "Gelecek Skoru": st.column_config.NumberColumn("Gelecek Skoru", format="%.1f"),
                "Özsermaye Karlılığı (ROE)": st.column_config.NumberColumn("Özsermaye Karlılığı (ROE)", format="%%%0.1f"),
                "F/K": st.column_config.NumberColumn("F/K", format="%.1f"),
                "PD/DD": st.column_config.NumberColumn("PD/DD", format="%.2f"),
                "Faaliyet Marjı %": st.column_config.NumberColumn("Faaliyet Marjı %", format="%%%0.1f"),
                "Süpürme %": st.column_config.NumberColumn("Süpürme %", format="%%%0.1f"),
                "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+0.2f%%"),
                "İvme Farkı": st.column_config.NumberColumn("İvme Farkı", format="%+0.1f")
            },
            use_container_width=True,
            hide_index=True
        )

else:
    st.info("🕒 Sistem başlatılıyor... Lütfen GitHub Actions üzerinden 'Run workflow' yapınız.")
