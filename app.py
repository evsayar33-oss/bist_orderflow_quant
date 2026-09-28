"""Streamlit dashboard — BIST Real-Return Engine V3 (read-only, reads ./data)."""
import json
import os

import pandas as pd
import streamlit as st

import config as C

st.set_page_config(page_title="BIST Reel Getiri Motoru V3", layout="wide", page_icon="🏛️")


@st.cache_data(ttl=120)
def _json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=120)
def _csv(path):
    return pd.read_csv(path, low_memory=False) if os.path.exists(path) else pd.DataFrame()


state = _json(C.STATE_FILE)
report = _json(C.BACKTEST_REPORT_FILE)
nav = _csv(C.NAV_FILE)
snaps = _csv(C.MONTHLY_SNAPSHOT_FILE)
trades = _csv(C.TRADE_LOG_FILE)

st.title("🏛️ BIST Reel Getiri Motoru V3")
st.caption("Uzun vadeli al-unut: 12 ayda TÜFE'yi yenmesi beklenen hisseler, aylık gözden geçirme. İkincil ölçüt: XU100.")
if not state:
    st.warning("Henüz V3 state yok. Actions: önce 'Walk Forward Backtest (V3)', sonra 'Daily Run' çalışmalı.")
    st.stop()

inf, reg, g, m, cal = (state.get(k, {}) for k in ("inflation", "regime", "autonomy_guard", "model", "calibration"))
perf = state.get("performance", {})
navm = perf.get("nav", {})
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("TÜFE (yıllık)", f"%{inf.get('yoy_pct')}", f"beklenen 12A %{inf.get('expected_12m_pct')}")
c2.metric("Rejim", reg.get("label", "?"), f"P(risk-off) %{float(reg.get('p_risk_off') or 0) * 100:.0f}")
c3.metric("Portföy reel getiri", f"%{navm.get('real_total_pct')}", f"nominal %{navm.get('total_return_pct')}")
c4.metric("XU100'e göre", f"{navm.get('excess_vs_xu100_cagr_pp')} puan/yıl", f"maks. düşüş %{navm.get('max_drawdown_pct')}")
c5.metric("Guard", g.get("mode", "?"), str(g.get("reason", ""))[:30])

t1, t2, t3, t4, t5 = st.tabs(["📌 Portföy", "💎 Aylık seçim", "📈 Performans", "🧠 Model", "📚 Backtest"])

with t1:
    pf = state.get("portfolio") or {}
    pos = pf.get("positions", {})
    if pos:
        navv = pf.get("nav", 1.0) or 1.0
        df = pd.DataFrame([{"Hisse": t, "Ağırlık %": round(p["value"] / navv * 100, 2),
                            "Getiri % (giriş sonrası)": round((p["level"] - 1) * 100, 2),
                            "Zirveden %": round((p["level"] / p["peak"] - 1) * 100, 2),
                            "Giriş": p["entry_date"], "Giriş skoru": p.get("entry_pct"),
                            "Beklenen reel 12A %": p.get("entry_exp_real")} for t, p in pos.items()])
        st.dataframe(df.sort_values("Ağırlık %", ascending=False), hide_index=True, use_container_width=True)
        st.caption(f"Nakit: %{pf.get('cash', 0) / navv * 100:.1f} | Felaket stopu: zirveden -%{C.CATASTROPHE_FROM_PEAK_PCT:.0f} "
                   f"veya girişten -%{C.CATASTROPHE_FROM_ENTRY_PCT:.0f}")
    else:
        st.info("Henüz pozisyon yok (ilk aylık gözden geçirmeden sonra açılır).")
    if pf.get("pending"):
        st.subheader("Yarın açılışta çalışacak emirler")
        st.dataframe(pd.DataFrame(pf["pending"]), hide_index=True, use_container_width=True)

with t2:
    if snaps.empty:
        st.info("Aylık kesit yok.")
    else:
        last = snaps[snaps["tarih"] == snaps["tarih"].max()]
        st.caption(f"Son aylık gözden geçirme: {snaps['tarih'].max()} | alım eşiği p{cal.get('pct_cutoff')} | tutma eşiği p{C.HOLD_PCT:.0f}")
        cols = [c for c in ["ticker", "sector", "composite_pct", "exp_real_12m", "p_beat_cpi", "vol_ann_pct",
                            "f_roe", "f_earnings_yield", "f_mom_12_1", "f_high_52w", "selected"] if c in last]
        st.dataframe(last.sort_values("composite", ascending=False)[cols].head(40), hide_index=True, use_container_width=True)

with t3:
    if not nav.empty:
        n = nav.copy()
        n["tarih"] = pd.to_datetime(n["tarih"])
        n = n.set_index("tarih")
        chart = pd.DataFrame({"Portföy": n["nav"] / n["nav"].iloc[0]})
        if "xu100" in n and n["xu100"].notna().any():
            x = n["xu100"].ffill().bfill()
            chart["XU100"] = x / x.iloc[0]
        st.line_chart(chart)
    st.json(navm)
    st.subheader("Kapanan pozisyonlar")
    st.json(perf.get("lots", {}))
    if not trades.empty:
        st.dataframe(trades.tail(50), hide_index=True, use_container_width=True)

with t4:
    w = m.get("champion_weights", {})
    if w:
        st.subheader("Faktör ağırlıkları")
        st.bar_chart(pd.Series(w).sort_values())
    st.json({"status": m.get("status"), "ic_live_12m": m.get("ic_live_12m"), "ic_live_3m": m.get("ic_live_3m"),
             "calibration_status": cal.get("status"), "bucket_table": cal.get("bucket_table"),
             "market": cal.get("market"), "regime": reg.get("probs")})

with t5:
    if not report:
        st.info("V3 backtest raporu yok.")
    else:
        st.caption(f"{report.get('period')} | {report.get('data_source')} | temel veri kapsamı {report.get('fundamentals_coverage')}")
        a, b = st.columns(2)
        a.subheader("Portföy (OOS)")
        a.json(report.get("portfolio", {}))
        b.subheader("Kapanan pozisyonlar")
        b.json(report.get("closed_lots", {}))
        if report.get("per_year"):
            st.subheader("Yıllık: nominal / TÜFE / reel / XU100")
            st.dataframe(pd.DataFrame(report["per_year"]).T, use_container_width=True)
        st.json({"oos_ic_12m": report.get("oos_composite_ic_12m"), "factor_ic_12m": report.get("factor_ic_full_sample_12m"),
                 "limitations": report.get("limitations")})
