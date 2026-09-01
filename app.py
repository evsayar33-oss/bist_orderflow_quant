import streamlit as st
import pandas as pd
import numpy as np
import os

st.set_page_config(page_title="BIST Bottom Accumulation Terminal", layout="wide", page_icon="🎯")

st.title("🎯 BIST Dip Akümülasyon & Taban Uyanış Terminali")
st.markdown("*Zirveye tırmanmış riskli hisseleri eleyen; **destek tabanında sıkışıp kurumsal alımla İLK DEFA kalkan** hisseleri bulan Quant Motoru.*")

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

    # ÇÖKME ENGELLEYİCİ GÜVENLİK
    for c in ['quant_score', 'score_diff', 'range_position', 'dist_from_support', 'sweep_ratio', 'vol_z', 'change_%', 'close']:
        if c not in df.columns: df[c] = 0.0
        else: df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).round(2)

    if 'regime' not in df.columns: df['regime'] = 'NÖTR'

    # SIDEBAR
    st.sidebar.header("🔍 Hisse Dip Konumu Sorgu")
    search_ticker = st.sidebar.text_input("Hisse Kodu (Örn: THYAO):").upper()
    
    if search_ticker:
        h_data = df[df['ticker'] == search_ticker]
        if not h_data.empty:
            score = float(h_data['quant_score'].iloc[0])
            diff = float(h_data['score_diff'].iloc[0])
            regime = h_data['regime'].iloc[0]
            r_pos = float(h_data['range_position'].iloc[0])
            d_supp = float(h_data['dist_from_support'].iloc[0])
            sweep = float(h_data['sweep_ratio'].iloc[0])
            
            st.sidebar.metric(f"{search_ticker} Dip Skoru", f"{score:.1f}", f"{diff:+.1f}")
            st.sidebar.write(f"**Durum:** {regime}")
            st.sidebar.write(f"**Taban Konumu:** Kanalın %{r_pos:.0f}'si")
            st.sidebar.write(f"**Dipten Uzaklık:** %{d_supp:+.1f}")
            st.sidebar.write(f"**Kurumsal Süpürme:** %{sweep:.1f}")
            
            st.sidebar.write("📈 Son 30 Günlük Skor:")
            trend = df_gecmis[df_gecmis['ticker'] == search_ticker][['tarih', 'quant_score']].sort_values('tarih')
            if not trend.empty:
                trend.set_index('tarih', inplace=True)
                st.sidebar.line_chart(trend['quant_score'])
        else:
            st.sidebar.warning("Hisse bulunamadı.")

    # 1. ANA TABLO: DİP AKÜMÜLASYON LİDERLERİ
    st.subheader("🎯 Dip Akümülasyon Liderleri (Tabandan Dönenler)")
    st.markdown("*Kanalın alt taban bölgesinde (%5-%45) sıkışmış, dipten yeni dönen taze hisseler.*")
    
    top_candidates = df[df['quant_score'] > 0.0].sort_values(by='quant_score', ascending=False).head(20)
    
    display_cols = ['ticker', 'quant_score', 'score_diff', 'regime', 'range_position', 'dist_from_support', 'vol_z', 'sweep_ratio', 'change_%', 'close']
    display_cols = [c for c in display_cols if c in df.columns]
    
    col_names = {
        'ticker': 'Hisse',
        'quant_score': 'Dip Skoru',
        'score_diff': 'İvme Farkı',
        'regime': 'Taban Durumu',
        'range_position': 'Taban Konumu %',
        'dist_from_support': 'Dipten Uzaklık %',
        'vol_z': 'Hacim Z',
        'sweep_ratio': 'Süpürme %',
        'change_%': 'Günlük %',
        'close': 'Fiyat (TL)'
    }
    
    if not top_candidates.empty:
        st.dataframe(
            top_candidates[display_cols].rename(columns=col_names),
            column_config={
                "Dip Skoru": st.column_config.ProgressColumn("Dip Skoru", min_value=0, max_value=100, format="%.1f"),
                "Taban Konumu %": st.column_config.NumberColumn("Taban Konumu %", format="%0.0f%%"),
                "Dipten Uzaklık %": st.column_config.NumberColumn("Dipten Uzaklık %", format="%+0.1f%%"),
                "Hacim Z": st.column_config.NumberColumn("Hacim Z", format="%+.2fσ"),
                "Süpürme %": st.column_config.NumberColumn("Süpürme %", format="%%%0.1f"),
                "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+0.2f%%"),
                "Fiyat (TL)": st.column_config.NumberColumn("Fiyat (TL)", format="%.2f TL"),
                "İvme Farkı": st.column_config.NumberColumn("İvme Farkı", format="%+0.1f")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("ℹ️ Bugün taban bölgesinden uyanış yapan taze bir hisse bulunamadı.")

    st.divider()

    # 2. DİSKALİFİYE EDİLENLER: ZİRVEDEKİ RİSKLİ HİSSELER
    st.subheader("🚫 Zirvedeki Riskli Hisseler (Uzak Dur)")
    st.markdown("*1 aylık kanalın en tepesine (%85+) dayanmış, tepe alımı riski taşıyan hisseler.*")
    
    traps = df[df['regime'].str.contains('ZİRVEDE|DUMP', na=False)].sort_values(by='range_position', ascending=False).head(15)
    if not traps.empty:
        st.dataframe(
            traps[display_cols].rename(columns=col_names),
            column_config={
                "Dip Skoru": st.column_config.NumberColumn("Dip Skoru", format="%.1f"),
                "Taban Konumu %": st.column_config.NumberColumn("Taban Konumu %", format="%0.0f%%"),
                "Dipten Uzaklık %": st.column_config.NumberColumn("Dipten Uzaklık %", format="%+0.1f%%"),
                "Hacim Z": st.column_config.NumberColumn("Hacim Z", format="%+.2fσ"),
                "Süpürme %": st.column_config.NumberColumn("Süpürme %", format="%%%0.1f"),
                "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+0.2f%%"),
                "Fiyat (TL)": st.column_config.NumberColumn("Fiyat (TL)", format="%.2f TL"),
                "İvme Farkı": st.column_config.NumberColumn("İvme Farkı", format="%+0.1f")
            },
            use_container_width=True,
            hide_index=True
        )

else:
    st.info("🕒 Sistem başlatılıyor... Lütfen GitHub Actions üzerinden 'Run workflow' yapınız.")
