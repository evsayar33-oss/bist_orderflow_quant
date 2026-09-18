import json
import os

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Adaptive BIST Orderflow Meta-Engine V1", layout="wide", page_icon="🧠")


def load_csv(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
        if "tarih" in df.columns:
            df["tarih"] = pd.to_datetime(df["tarih"], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


def load_state():
    try:
        with open("longterm_ai_state.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

state = load_state()
history = load_csv("gecmis_veri.csv")
signals = load_csv("signals_log.csv")
lifecycle = load_csv("signals_lifecycle.csv")

st.title("🧠 Adaptive BIST Orderflow Meta-Engine V1")
st.caption("Pre-move flow/activity detection + regime adaptation + resilience + controlled self-learning")

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.metric("Rejim", state.get("market_regime", "NEUTRAL"))
with c2:
    st.metric("Rejim Güveni", f"%{float(state.get('regime_confidence', 0)):.1f}")
with c3:
    st.metric("T+3 Win Rate", f"%{float(state.get('audit_summary', {}).get('win_rate_t3', 0)):.1f}")
with c4:
    st.metric("Aktif Model", state.get("validation", {}).get("active_model_version", "champion-1"))

st.divider()

if not history.empty:
    latest = history[history["tarih"] == history["tarih"].max()].copy()
else:
    latest = pd.DataFrame()

st.subheader("💎 Güncel Meta Candidates")
if not latest.empty and "eligible" in latest.columns:
    view = latest[latest["eligible"] == True].copy().sort_values("meta_score", ascending=False).head(20)
    cols = [c for c in ["ticker", "meta_score", "pre_move_score", "flow_score", "resilience_score", "regime_fit_score", "risk_score", "quality_score", "rvol", "value_traded"] if c in view.columns]
    st.dataframe(view[cols], use_container_width=True, hide_index=True)
else:
    st.info("Henüz güvenilir bir günlük tarama kaydı yok.")

st.subheader("🧠 Model Weights")
weights = state.get("effective_weights", state.get("weights", {}))
if weights:
    st.dataframe(pd.DataFrame([weights]), use_container_width=True, hide_index=True)

st.subheader("🛡️ Lifecycle")
if not lifecycle.empty:
    cols = [c for c in ["tarih", "ticker", "entry_price", "initial_stop_price", "current_stop_price", "meta_score", "risk_score", "max_favorable_excursion", "max_adverse_excursion", "outcome"] if c in lifecycle.columns]
    st.dataframe(lifecycle.sort_values("tarih", ascending=False).head(30)[cols], use_container_width=True, hide_index=True)
else:
    st.info("Lifecycle kaydı henüz oluşmadı.")

st.subheader("📚 Learning / Validation")
st.json({
    "audit_summary": state.get("audit_summary", {}),
    "validation": state.get("validation", {}),
    "data_quality": state.get("data_quality", {}),
    "regime_stats": state.get("regime_stats", {}),
})
