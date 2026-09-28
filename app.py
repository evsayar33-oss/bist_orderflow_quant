"""Streamlit dashboard for Meta-Engine V2 (read-only; reads ./data)."""
import json
import os

import pandas as pd
import streamlit as st

import config as C

st.set_page_config(page_title="BIST Meta-Engine V2", layout="wide", page_icon="🧠")


@st.cache_data(ttl=120)
def _json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=120)
def _csv(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


state = _json(C.STATE_FILE)
report = _json(C.BACKTEST_REPORT_FILE)
prior = _json(C.RESEARCH_PRIOR_FILE)
ledger = _csv(C.LEDGER_FILE)
snaps = _csv(C.SNAPSHOT_FILE)

st.title("🧠 Adaptive BIST Orderflow Meta-Engine V2")
if not state:
    st.warning("Henüz state yok. GitHub Actions'ta önce 'Walk Forward Backtest', sonra 'Daily Scan' çalışmalı.")
    st.stop()

reg, g, model, cal = state.get("regime", {}), state.get("autonomy_guard", {}), state.get("model", {}), state.get("calibration", {})
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Rejim (HMM)", reg.get("label", "?"), f"P(risk-off) %{float(reg.get('p_risk_off') or 0) * 100:.0f}")
c2.metric("Beklenen piyasa 5G", f"%{float(reg.get('exp_mkt_5d_pct') or 0):+.2f}", reg.get("source", ""))
c3.metric("Guard", g.get("mode", "?"), g.get("reason", ""))
c4.metric("Model", str(model.get("champion_version", "?")), str(model.get("status", ""))[:40])
c5.metric("Kalibrasyon", str(cal.get("status", "?")), f"eşik p{cal.get('pct_cutoff', '?')}")

tab1, tab2, tab3, tab4 = st.tabs(["💎 Adaylar", "💼 Pozisyonlar", "🔬 Model", "📚 Backtest"])

with tab1:
    if snaps.empty:
        st.info("Snapshot yok.")
    else:
        last = snaps[snaps["tarih"] == snaps["tarih"].max()].copy()
        st.caption(f"Son kapanış verisi: {snaps['tarih'].max()} — adaylar ertesi seans açılışı içindir.")
        el = last[last.get("eligible", False) == True] if "eligible" in last else last.head(0)  # noqa: E712
        cols = [c for c in ["ticker", "close", "change_pct", "composite_pct", "exp_net_pct", "atr", "value_traded"] if c in last]
        st.subheader("Seçilen adaylar")
        st.dataframe(el[cols], hide_index=True, use_container_width=True)
        st.subheader("Skor sıralaması (ilk 30)")
        st.dataframe(last.sort_values("composite", ascending=False)[cols].head(30), hide_index=True, use_container_width=True)

with tab2:
    perf = state.get("performance", {})
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Kapanan işlem", perf.get("closed", 0))
    p2.metric("İsabet", f"%{perf.get('hit_rate_pct', 0)}")
    p3.metric("Ort. net", f"%{perf.get('avg_net_pct', 0)}")
    p4.metric("Profit factor", perf.get("profit_factor"))
    if not ledger.empty:
        st.subheader("Açık / bekleyen")
        st.dataframe(ledger[ledger["status"].isin(["OPEN", "PENDING_ENTRY"])], hide_index=True, use_container_width=True)
        st.subheader("Kapananlar (son 50)")
        st.dataframe(ledger[ledger["status"] == "CLOSED"].sort_values("exit_date", ascending=False).head(50),
                     hide_index=True, use_container_width=True)

with tab3:
    w = model.get("champion_weights", {})
    if w:
        st.subheader("Champion faktör ağırlıkları (işaretli)")
        st.bar_chart(pd.Series(w).sort_values())
    ics = model.get("ic_stats", {})
    if ics:
        st.subheader("Canlı faktör IC (Newey-West)")
        st.dataframe(pd.DataFrame(ics).T, use_container_width=True)
    li = model.get("composite_ic_live", {})
    if li.get("series_tail"):
        st.subheader("Canlı composite IC (kaydedildiği hâliyle, OOS)")
        st.line_chart(pd.Series(li["series_tail"]))
    st.json({"challenger": model.get("challenger"), "calibration": cal.get("cutoffs"), "regime": reg.get("probs")})

with tab4:
    if not report:
        st.info("Backtest raporu yok.")
    else:
        st.caption(f"{report.get('period')} | {report.get('data_source')}")
        a, b = st.columns(2)
        a.json(report.get("oos_trades", {}))
        b.json(report.get("oos_portfolio", {}))
        st.json({"oos_composite_ic": report.get("oos_composite_ic"), "stability": report.get("stability"),
                 "limitations": report.get("limitations")})
        if report.get("folds"):
            st.dataframe(pd.DataFrame([{**{k: v for k, v in f.items() if k not in ("weights", "trades")},
                                        **{f"t_{k}": v for k, v in (f.get("trades") or {}).items() if k != "exit_mix"}}
                                       for f in report["folds"]]), hide_index=True, use_container_width=True)
