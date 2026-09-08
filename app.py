import streamlit as st
import pandas as pd
import numpy as np
import os
import json

st.set_page_config(
    page_title="BIST Multi-Bagger Kuluçka Terminali",
    layout="wide",
    page_icon="🦅"
)

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
# BAŞLIK VE METRİKLER
# =============================================================================

st.title("🦅 BIST Multi-Bagger & Kuluçka Terminali")
st.markdown("*52 haftalık makro dipte kuluçkaya yatmış; **kâr patlaması yaşayan ve %100 - %250 potansiyel taşıyan Small/Mid-Cap** şirketleri tespit eden Quant Motoru.*")

col1, col2, col3, col4 = st.columns(4)
weights = ai_state.get("weights", {"macro_base": 0.35, "growth_quality": 0.30, "stealth_accumulation": 0.20, "volume_ignition": 0.15})
audit = ai_state.get("audit_summary", {})

with col1:
    st.metric("🤖 Model Durumu", "Aktif", audit.get("status", "Kuluçka Takibinde")[:22] + "...")
with col2:
    st.metric("🏆 6 Aylık Win Rate", f"%{audit.get('win_rate_6m', 0.0):.1f}", f"Denetlenen: {audit.get('total_signals_audited', 0)}")
with col3:
    st.metric("🎯 Hedef Skalası", "%100 - %250", "Buy & Hold (6-12 Ay)")
with col4:
    last_date = audit.get("last_audit_date", "-")
    st.metric("🗓️ Son Güncelleme", str(last_date))

st.divider()

# =============================================================================
# YAN PANEL (SIDEBAR)
# =============================================================================

st.sidebar.header("🔍 Hisse Kuluçka Sorgu")
search_ticker = st.sidebar.text_input("Hisse Kodu Girin (Örn: RAYSG):").upper().strip()

if not df_gecmis.empty:
    son_tarih = df_gecmis['tarih'].max()
    df_latest = df_gecmis[df_gecmis['tarih'] == son_tarih].copy()
    
    if search_ticker:
        h_data = df_latest[df_latest['ticker'] == search_ticker]
        if not h_data.empty:
            score = float(h_data['quant_score'].iloc[0])
            regime = h_data['regime'].iloc[0]
            d_52w = float(h_data.get('dist_from_52w_low', 0.0).iloc[0])
            mcap = float(h_data.get('mcap_milyar', 0.0).iloc[0])
            target_1 = float(h_data.get('target_cup', 0.0).iloc[0])
            target_2 = float(h_data.get('target_bagger', 0.0).iloc[0])
            roe = float(h_data.get('roe', 0.0).iloc[0])
            pe = float(h_data.get('pe', 0.0).iloc[0])

            st.sidebar.metric(f"{search_ticker} Kuluçka Skoru", f"{score:.1f}")
            st.sidebar.write(f"**Durum:** {regime}")
            st.sidebar.write(f"**Piyasa Değeri:** {mcap:.1f} Milyar TL")
            st.sidebar.write(f"**52H Dip Mesafesi:** %{d_52w:+.1f}")
            st.sidebar.write(f"**ROE:** %{roe:.1f} | **F/K:** {pe:.1f}")
            st.sidebar.write(f"🎯 **1. Çanak Hedefi:** {target_1:.2f} TL")
            st.sidebar.write(f"🚀 **2. Bagger Hedefi:** {target_2:.2f} TL")

            trend = df_gecmis[df_gecmis['ticker'] == search_ticker][['tarih', 'quant_score']].sort_values('tarih')
            if not trend.empty:
                trend.set_index('tarih', inplace=True)
                st.sidebar.line_chart(trend['quant_score'])
        else:
            st.sidebar.warning("Hisse bugünkü taramada bulunamadı.")

# =============================================================================
# SEKMELER
# =============================================================================

tab_leads, tab_ai, tab_risks = st.tabs([
    "💎 Kuluçka Liderleri (Multi-Baggers)",
    "🧠 Quant AI & Portföy Takip Defteri",
    "🏢 Elenen Hisseler (Devler & Zombiler)"
])

with tab_leads:
    st.subheader("💎 52 Haftalık Dipte Kuluçkaya Yatan Şirketler")
    st.markdown("*Piyasa değeri 2-35 Mr TL arası, ROE'si %20'nin üzerinde ve 52 haftalık dip desteğinde kurumsal alım gören hisseler.*")
    
    if not df_gecmis.empty:
        leaders = df_latest[
            df_latest['regime'].str.contains("KULUÇKA LİDERİ", na=False)
        ].sort_values(by='quant_score', ascending=False).head(15)

        col_map = {
            'ticker': 'Hisse',
            'quant_score': 'Kuluçka Skoru',
            'close': 'Fiyat (TL)',
            'mcap_milyar': 'Piyasa Değeri (Mr TL)',
            'dist_from_52w_low': '52H Dip %',
            'target_cup': '1. Çanak Hedefi',
            'potansiyel_cup': 'Çanak Prim %',
            'target_bagger': '2. Bagger (2.5x)',
            'stop_price': 'Taban Stop',
            'roe': 'ROE %',
            'pe': 'F/K'
        }
        
        display_cols = [c for c in col_map.keys() if c in leaders.columns]
        
        if not leaders.empty:
            st.dataframe(
                leaders[display_cols].rename(columns=col_map),
                column_config={
                    "Kuluçka Skoru": st.column_config.ProgressColumn("Kuluçka Skoru", min_value=0, max_value=100, format="%.1f"),
                    "Fiyat (TL)": st.column_config.NumberColumn("Fiyat (TL)", format="%.2f TL"),
                    "52H Dip %": st.column_config.NumberColumn("52H Dip %", format="%+0.1f%%"),
                    "Çanak Prim %": st.column_config.NumberColumn("Çanak Prim %", format="%+0.0f%%"),
                    "1. Çanak Hedefi": st.column_config.NumberColumn("1. Çanak Hedefi", format="%.2f TL"),
                    "2. Bagger (2.5x)": st.column_config.NumberColumn("2. Bagger (2.5x)", format="%.2f TL"),
                    "Taban Stop": st.column_config.NumberColumn("Taban Stop", format="%.2f TL"),
                    "ROE %": st.column_config.NumberColumn("ROE %", format="%%%0.1f"),
                    "F/K": st.column_config.NumberColumn("F/K", format="%.1f")
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Bugün kuluçka şartlarını sağlayan hisse bulunamadı.")

with tab_ai:
    st.subheader("🧠 Model Faktör Dağılımı (Dinamik Ağırlıklar)")
    
    col_w1, col_w2, col_w3, col_w4 = st.columns(4)
    with col_w1:
        st.write(f"**52H Taban Geometrisi:** %{int(weights.get('macro_base', 0.35)*100)}")
        st.progress(float(weights.get('macro_base', 0.35)))
    with col_w2:
        st.write(f"**Büyüme & Kârlılık (ROE):** %{int(weights.get('growth_quality', 0.30)*100)}")
        st.progress(float(weights.get('growth_quality', 0.30)))
    with col_w3:
        st.write(f"**Sessiz Kurumsal Takas:** %{int(weights.get('stealth_accumulation', 0.20)*100)}")
        st.progress(float(weights.get('stealth_accumulation', 0.20)))
    with col_w4:
        st.write(f"**Hacimli Ateşleme:** %{int(weights.get('volume_ignition', 0.15)*100)}")
        st.progress(float(weights.get('volume_ignition', 0.15)))

    st.write("")
    st.subheader("📋 Sinyal Yaşam Döngüsü & Kâr Koruma Takibi")
    
    if not df_lifecycle.empty:
        recent_lifecycle = df_lifecycle.sort_values(by='tarih', ascending=False).head(30)
        life_map = {
            'tarih': 'Sinyal Tarihi',
            'ticker': 'Hisse',
            'entry_price': 'Giriş Fiyatı',
            'stop_price': 'Güncel İzleyen Stop',
            'target_cup': '1. Çanak Hedefi',
            'max_drawdown': 'Max Çekilme %',
            'peak_gain': 'Görülen Tepe Kâr %',
            'outcome': 'Kuluçka Durumu'
        }
        l_cols = [c for c in life_map.keys() if c in recent_lifecycle.columns]
        
        st.dataframe(
            recent_lifecycle[l_cols].rename(columns=life_map),
            column_config={
                "Sinyal Tarihi": st.column_config.DateColumn("Sinyal Tarihi", format="YYYY-MM-DD"),
                "Giriş Fiyatı": st.column_config.NumberColumn("Giriş Fiyatı", format="%.2f TL"),
                "Güncel İzleyen Stop": st.column_config.NumberColumn("Güncel İzleyen Stop", format="%.2f TL"),
                "1. Çanak Hedefi": st.column_config.NumberColumn("1. Çanak Hedefi", format="%.2f TL"),
                "Max Çekilme %": st.column_config.NumberColumn("Max Çekilme %", format="%0.1f%%"),
                "Görülen Tepe Kâr %": st.column_config.NumberColumn("Görülen Tepe Kâr %", format="%+0.1f%%")
            },
            use_container_width=True,
            hide_index=True
        )

with tab_risks:
    st.subheader("🏢 Diskalifiye Edilen Hisseler")
    st.markdown("*3x yapma potansiyeli olmayan 50 Milyar TL üstü hantal mega devler ve kârsız zombi şirketler.*")
    
    if not df_gecmis.empty:
        traps = df_latest[
            df_latest['regime'].str.contains("MEGA DEV|ELENDİ", na=False)
        ].head(25)

        if not traps.empty:
            r_cols = ['ticker', 'regime', 'mcap_milyar', 'roe', 'close']
            r_cols = [c for c in r_cols if c in traps.columns]
            st.dataframe(
                traps[r_cols].rename(columns={
                    'ticker': 'Hisse',
                    'regime': 'Elenme Sebebi',
                    'mcap_milyar': 'Piyasa Değeri (Mr TL)',
                    'roe': 'ROE %',
                    'close': 'Fiyat'
                }),
                use_container_width=True,
                hide_index=True
            )
