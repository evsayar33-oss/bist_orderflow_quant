import streamlit as st
import pandas as pd
import numpy as np
import os
import json

st.set_page_config(
    page_title="BIST Swing & Taban Akümülasyon Terminali",
    layout="wide",
    page_icon="🎯"
)

# =============================================================================
# VERİ YÜKLEME FONKSİYONLARI
# =============================================================================

@st.cache_data(ttl=60)
def load_historical_data():
    if os.path.exists("gecmis_veri.csv"):
        try:
            df = pd.read_csv("gecmis_veri.csv")
            if 'tarih' in df.columns:
                df['tarih'] = pd.to_datetime(df['tarih'])
            return df
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

@st.cache_data(ttl=60)
def load_ai_state():
    if os.path.exists("longterm_ai_state.json"):
        try:
            with open("longterm_ai_state.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

@st.cache_data(ttl=60)
def load_lifecycle_signals():
    if os.path.exists("signals_lifecycle.csv"):
        try:
            df = pd.read_csv("signals_lifecycle.csv")
            df['tarih'] = pd.to_datetime(df['tarih'])
            return df
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

df_gecmis = load_historical_data()
ai_state = load_ai_state()
df_lifecycle = load_lifecycle_signals()

# =============================================================================
# BAŞLIK VE ÜST BİLGİ ALANI
# =============================================================================

st.title("🎯 BIST Swing & Taban Akümülasyon Terminali")
st.markdown("*Düşen bıçakları eleyen, **destek tabanında sıkışıp kurumsal alımla orta/uzun vadeli trend başlatan** hisseleri bulan Quant Motoru.*")

# Üst Bilgi Kartları
col1, col2, col3, col4 = st.columns(4)

weights = ai_state.get("weights", {"accum": 0.35, "fundamental": 0.25, "sweep": 0.25, "vol_mom": 0.15})
audit = ai_state.get("audit_summary", {})

with col1:
    st.metric("🤖 AI Durumu", "Aktif", audit.get("status", "Öğreniyor")[:22] + "...")
with col2:
    st.metric("🏆 30G Win Rate", f"%{audit.get('win_rate_30d', 0.0):.1f}", f"Denetlenen: {audit.get('total_signals_audited', 0)}")
with col3:
    st.metric("🛡️ Ort. Max Drawdown", f"%{audit.get('avg_max_drawdown', 0.0):.1f}", "Stop Koruması")
with col4:
    last_date = audit.get("last_audit_date", "-")
    st.metric("🗓️ Son Model Denetimi", str(last_date))

st.divider()

# =============================================================================
# YAN PANEL (SIDEBAR) - HİSSE SORGULAMA
# =============================================================================

st.sidebar.header("🔍 Hisse Konum & Temel Sorgu")
search_ticker = st.sidebar.text_input("Hisse Kodu Girin (Örn: THYAO):").upper().strip()

if not df_gecmis.empty:
    son_tarih = df_gecmis['tarih'].max()
    df_latest = df_gecmis[df_gecmis['tarih'] == son_tarih].copy()
    
    if search_ticker:
        h_data = df_latest[df_latest['ticker'] == search_ticker]
        if not h_data.empty:
            score = float(h_data['quant_score'].iloc[0])
            diff = float(h_data.get('score_diff', 0.0).iloc[0])
            regime = h_data['regime'].iloc[0]
            d_bottom = float(h_data.get('dist_from_bottom', 0.0).iloc[0])
            sweep = float(h_data.get('score_sweep', 0.0).iloc[0])
            fund = float(h_data.get('score_fund', 0.0).iloc[0])
            roe = float(h_data.get('roe', 0.0).iloc[0])

            st.sidebar.metric(f"{search_ticker} Quant Skoru", f"{score:.1f}", f"{diff:+.1f}")
            st.sidebar.write(f"**Durum:** {regime}")
            st.sidebar.write(f"**Dipten Uzaklık:** %{d_bottom:+.1f}")
            st.sidebar.write(f"**Temel Skor:** {fund:.0f}/100 (ROE: %{roe:.1f})")
            st.sidebar.write(f"**Kurumsal Takas:** %{sweep:.1f}")

            st.sidebar.write("📈 **Son 30 Günlük Skor Eğilimi:**")
            trend = df_gecmis[df_gecmis['ticker'] == search_ticker][['tarih', 'quant_score']].sort_values('tarih')
            if not trend.empty:
                trend.set_index('tarih', inplace=True)
                st.sidebar.line_chart(trend['quant_score'])
        else:
            st.sidebar.warning("Hisse bugünkü taramada bulunamadı.")

# =============================================================================
# ANA SEKMELER
# =============================================================================

tab_leads, tab_ai, tab_risks = st.tabs([
    "🎯 Dip Akümülasyon Liderleri",
    "🧠 Quant AI & Model Karnesi",
    "🪤 Düşen Bıçaklar & Riskler"
])

# -----------------------------------------------------------------------------
# SEKME 1: DİP AKÜMÜLASYON LİDERLERİ
# -----------------------------------------------------------------------------
with tab_leads:
    st.subheader("🎯 Swing & Taban Dönüş Adayları")
    st.markdown("*Düşen bıçak filtresinden geçmiş, dipten dönüşünü teyit etmiş ve kurumsal alımla desteklenen hisseler.*")
    
    if not df_gecmis.empty:
        top_candidates = df_latest[
            (df_latest['quant_score'] >= 60.0) & 
            (~df_latest['regime'].str.contains("DÜŞEN BIÇAK|BOŞALTIM", na=False))
        ].sort_values(by='quant_score', ascending=False).head(20)

        col_map = {
            'ticker': 'Hisse',
            'quant_score': 'Quant Skoru',
            'score_diff': 'İvme',
            'regime': 'Durum',
            'dist_from_bottom': 'Dipten Kalkış %',
            'score_fund': 'Temel Kalite',
            'score_sweep': 'Kurumsal Takas %',
            'vol_z': 'Hacim Z',
            'change_%': 'Günlük %',
            'close': 'Fiyat (TL)'
        }
        
        display_cols = [c for c in col_map.keys() if c in top_candidates.columns]
        
        if not top_candidates.empty:
            st.dataframe(
                top_candidates[display_cols].rename(columns=col_map),
                column_config={
                    "Quant Skoru": st.column_config.ProgressColumn("Quant Skoru", min_value=0, max_value=100, format="%.1f"),
                    "Temel Kalite": st.column_config.ProgressColumn("Temel Kalite", min_value=0, max_value=100, format="%.0f"),
                    "Dipten Kalkış %": st.column_config.NumberColumn("Dipten Kalkış %", format="%+0.1f%%"),
                    "Kurumsal Takas %": st.column_config.NumberColumn("Kurumsal Takas %", format="%%%0.1f"),
                    "Hacim Z": st.column_config.NumberColumn("Hacim Z", format="%+.2fσ"),
                    "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+0.2f%%"),
                    "Fiyat (TL)": st.column_config.NumberColumn("Fiyat (TL)", format="%.2f TL"),
                    "İvme": st.column_config.NumberColumn("İvme", format="%+0.1f")
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Bugün taban filtrelerine uyan taze hisse bulunamadı.")
    else:
        st.info("Henüz taranmış veri bulunmuyor.")

# -----------------------------------------------------------------------------
# SEKME 2: QUANT AI & DENETÇİ KARNESİ
# -----------------------------------------------------------------------------
with tab_ai:
    st.subheader("🧠 Otonom Öğrenme ve Faktör Dağılımı")
    st.markdown("*Model geçmiş sinyallerin T+15, T+30 ve T+60 gün performansını denetleyerek ağırlıkları kendi günceller.*")
    
    # Ağırlık Barları
    col_w1, col_w2, col_w3, col_w4 = st.columns(4)
    with col_w1:
        st.write(f"**Taban Geometrisi:** %{int(weights.get('accum', 0.35)*100)}")
        st.progress(float(weights.get('accum', 0.35)))
    with col_w2:
        st.write(f"**Temel Sağlamlık:** %{int(weights.get('fundamental', 0.25)*100)}")
        st.progress(float(weights.get('fundamental', 0.25)))
    with col_w3:
        st.write(f"**Kurumsal Takas:** %{int(weights.get('sweep', 0.25)*100)}")
        st.progress(float(weights.get('sweep', 0.25)))
    with col_w4:
        st.write(f"**Hacim & İvme:** %{int(weights.get('vol_mom', 0.15)*100)}")
        st.progress(float(weights.get('vol_mom', 0.15)))

    st.write("")
    st.subheader("📋 Sinyal Yaşam Döngüsü & Denetim Defteri (Lifecycle)")
    
    if not df_lifecycle.empty:
        recent_lifecycle = df_lifecycle.sort_values(by='tarih', ascending=False).head(30)
        life_map = {
            'tarih': 'Sinyal Tarihi',
            'ticker': 'Hisse',
            'entry_price': 'Giriş Fiyatı',
            'quant_score': 'Giriş Skoru',
            'ret_15d': 'T+15G %',
            'ret_30d': 'T+30G %',
            'ret_60d': 'T+60G %',
            'max_drawdown': 'Max DD %',
            'peak_gain': 'Tepe Kâr %',
            'outcome': 'Sonuç'
        }
        l_cols = [c for c in life_map.keys() if c in recent_lifecycle.columns]
        
        st.dataframe(
            recent_lifecycle[l_cols].rename(columns=life_map),
            column_config={
                "Sinyal Tarihi": st.column_config.DateColumn("Sinyal Tarihi", format="YYYY-MM-DD"),
                "Giriş Fiyatı": st.column_config.NumberColumn("Giriş Fiyatı", format="%.2f TL"),
                "Giriş Skoru": st.column_config.NumberColumn("Giriş Skoru", format="%.1f"),
                "T+15G %": st.column_config.NumberColumn("T+15G %", format="%+0.1f%%"),
                "T+30G %": st.column_config.NumberColumn("T+30G %", format="%+0.1f%%"),
                "T+60G %": st.column_config.NumberColumn("T+60G %", format="%+0.1f%%"),
                "Max DD %": st.column_config.NumberColumn("Max DD %", format="%0.1f%%"),
                "Tepe Kâr %": st.column_config.NumberColumn("Tepe Kâr %", format="%+0.1f%%")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("Yaşam döngüsü defterinde henüz sinyal bulunmuyor.")

# -----------------------------------------------------------------------------
# SEKME 3: DÜŞEN BIÇAKLAR & RİSKLER
# -----------------------------------------------------------------------------
with tab_risks:
    st.subheader("🪤 Diskalifiye Edilenler (Düşen Bıçak & Zirve Riskleri)")
    st.markdown("*Sistemin serbest düşüşte olduğu (son 1 ayda >-%18) veya taban kırmış olduğu için kapı dışarı ettiği hisseler.*")
    
    if not df_gecmis.empty:
        traps = df_latest[
            df_latest['regime'].str.contains("DÜŞEN BIÇAK|BOŞALTIM", na=False)
        ].sort_values(by='change_%', ascending=True).head(20)

        if not traps.empty:
            r_cols = ['ticker', 'regime', 'change_%', 'close', 'dist_from_bottom', 'score_fund']
            r_cols = [c for c in r_cols if c in traps.columns]
            st.dataframe(
                traps[r_cols].rename(columns={
                    'ticker': 'Hisse',
                    'regime': 'Risk Sebebi',
                    'change_%': 'Günlük %',
                    'close': 'Fiyat',
                    'dist_from_bottom': 'Dipten Mesafe %',
                    'score_fund': 'Temel Kalite'
                }),
                column_config={
                    "Günlük %": st.column_config.NumberColumn("Günlük %", format="%+0.2f%%"),
                    "Fiyat": st.column_config.NumberColumn("Fiyat", format="%.2f TL")
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.success("Bugün piyasada sıra dışı serbest düşüşe geçen hisse tespit edilmedi.")
