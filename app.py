"""Streamlit dashboard — BIST Real-Return Engine V3 (read-only, reads ./data)."""
import json
import math
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


def fmt(v, prefix="%", nd=1, sign=False):
    try:
        x = float(v)
        if math.isnan(x):
            return "—"
        return f"{prefix}{x:+.{nd}f}" if sign else f"{prefix}{x:.{nd}f}"
    except (TypeError, ValueError):
        return "—"


state = _json(C.STATE_FILE)
report = _json(C.BACKTEST_REPORT_FILE)
nav = _csv(C.NAV_FILE)
snaps = _csv(C.MONTHLY_SNAPSHOT_FILE)
trades = _csv(C.TRADE_LOG_FILE)

st.title("🏛️ BIST Reel Getiri Motoru V3")
st.caption("Uzun vadeli al-unut: 12 ayda TÜFE'yi yenmesi beklenen hisseler, aylık gözden geçirme. İkincil ölçüt: XU100.")
if not state:
    st.warning("Henüz V3 state yok. Actions: önce 'Walk Forward Backtest', sonra 'Daily Run' çalışmalı.")
    st.stop()

inf, reg, g, m, cal = (state.get(k, {}) or {} for k in ("inflation", "regime", "autonomy_guard", "model", "calibration"))
perf = state.get("performance", {}) or {}
navm = perf.get("nav", {}) or {}
pf = state.get("portfolio") or {}
weights = m.get("champion_weights", {}) or {}

# ---------------------------------------------------------------- status banners
cpi_status = inf.get("status")
if inf.get("expected_12m_pct") is None:
    st.error("⛔ TÜFE verisi alınamadı → reel getiri hesaplanamıyor ve **yeni alım yapılmıyor**. "
             "Çözüm: evds3.tcmb.gov.tr → Profilim → API Key Kopyala, sonra GitHub → Settings → Secrets → Actions → "
             "`EVDS_API_KEY` ekleyin.")
elif cpi_status == "PROXY":
    st.warning("⚠️ Resmî TÜFE yerine USDTRY vekili kullanılıyor (+5 puan güvenlik payıyla). "
               "Kesin sonuç için `EVDS_API_KEY` secret'ını ekleyin.")
elif cpi_status == "STALE":
    st.warning(f"⚠️ TÜFE serisi güncel değil (son ay {inf.get('last_month')}). `EVDS_API_KEY` ekleyin.")
if navm.get("days", 0) < 2:
    st.info(f"ℹ️ Portföy {pf.get('start_date', '—')} tarihinde başladı; performans metrikleri ilk işlem günlerinden sonra dolacak.")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("TÜFE (yıllık)", fmt(inf.get("yoy_pct")), f"beklenen 12A {fmt(inf.get('expected_12m_pct'))} · {inf.get('source') or 'yok'}")
c2.metric("Rejim", reg.get("label", "—"), f"P(risk-off) {fmt((reg.get('p_risk_off') or 0) * 100, nd=0)}")
c3.metric("Portföy reel getiri", fmt(navm.get("real_total_pct"), sign=True), f"nominal {fmt(navm.get('total_return_pct'), sign=True)}")
_ex = fmt(navm.get("excess_vs_xu100_cagr_pp"), prefix="", sign=True)
c4.metric("XU100'e göre (yıllık)", _ex if _ex == "—" else _ex + " puan",
          f"maks. düşüş {fmt(navm.get('max_drawdown_pct'))}")
c5.metric("Guard", g.get("mode", "—"), str(g.get("reason", ""))[:30])


# ---------------------------------------------------------------- stock search
def render_stock(t: str):
    last_date = snaps["tarih"].max()
    last = snaps[snaps["tarih"] == last_date]
    row = last[last["ticker"] == t]
    pos = (pf.get("positions") or {}).get(t)
    pend = [o for o in (pf.get("pending") or []) if o["ticker"] == t]
    st.subheader(f"{t}")
    if pos:
        navv = pf.get("nav", 1.0) or 1.0
        st.success(f"📌 Portföyde | ağırlık %{pos['value'] / navv * 100:.1f} | girişten {fmt((pos['level'] - 1) * 100, sign=True)} "
                   f"| zirveden {fmt((pos['level'] / pos['peak'] - 1) * 100, sign=True)} | giriş {pos['entry_date']}")
    for o in pend:
        st.info(f"🕒 Bekleyen emir (yarın açılış): {o['action']} — {o['reason']} — hedef ağırlık %{o.get('target_w', 0) * 100:.1f}")
    if row.empty:
        st.warning(f"{t} son aylık taramada ({last_date}) puanlanmadı (likidite/fiyat geçmişi yetersiz ya da listede değil).")
        return
    r = row.iloc[0]
    n = len(last)
    rank = int((last["composite"] > r["composite"]).sum()) + 1
    cutoff = float(cal.get("pct_cutoff", C.BUY_PCT))
    a, b, c, d = st.columns(4)
    a.metric("Skor yüzdeliği", fmt(r.get("composite_pct"), prefix="p", nd=0), f"sıra {rank}/{n}")
    b.metric("Beklenen reel 12A", fmt(r.get("exp_real_12m"), sign=True),
             None if cal.get("status") == "CALIBRATED" else "kalibrasyon yok: yalnız piyasa beklentisi")
    c.metric("TÜFE'yi yenme olasılığı", fmt((r.get("p_beat_cpi") or float("nan")) * 100, nd=0))
    d.metric("Yıllık oynaklık", fmt(r.get("vol_ann_pct")), f"beta {fmt(r.get('beta'), prefix='', nd=2)}")
    reasons = []
    if bool(r.get("selected")):
        reasons.append("✅ Bu ay portföy için SEÇİLDİ (al ya da tut).")
    else:
        if float(r.get("composite_pct", 0)) < cutoff:
            reasons.append(f"Skor alım eşiğinin altında (p{r.get('composite_pct', 0):.0f} < p{cutoff:.0f}).")
        er = r.get("exp_real_12m")
        if er is None or (isinstance(er, float) and math.isnan(er)):
            reasons.append("Beklenen reel getiri hesaplanamadı (TÜFE verisi yok).")
        elif float(er) < C.MIN_EXPECTED_REAL_PCT:
            reasons.append(f"Beklenen reel getiri eşiğin altında (%{float(er):.1f} < %{C.MIN_EXPECTED_REAL_PCT:.0f}).")
        if float(r.get("med_value_traded") or 0) < C.MIN_MEDIAN_VALUE_TRADED_TL:
            reasons.append("Likidite (3 aylık medyan işlem hacmi) yetersiz.")
        if str(r.get("fund_break")).lower() == "true":
            reasons.append("Temel bozulma: zarar + negatif ROE.")
        if not reasons:
            reasons.append("Kriterleri geçti ama sektör limiti / hedef pozisyon sayısı / maruziyet nedeniyle alınmadı.")
    st.markdown("**Karar gerekçesi:** " + " ".join(reasons))
    sec = r.get("sector")
    if isinstance(sec, str):
        peers = last[last["sector"] == sec].sort_values("composite", ascending=False)
        st.caption(f"Sektör: {sec} — sektör içi sıra {int((peers['composite'] > r['composite']).sum()) + 1}/{len(peers)}")
    rows = []
    for k in C.FACTORS:
        z = r.get(f"z_{k}")
        w = weights.get(k, 0.0)
        rows.append({"Faktör": k, "Ham değer": r.get(f"f_{k}"), "z-skor": z, "Ağırlık": w,
                     "Katkı (w·z)": (w * z) if z is not None and not (isinstance(z, float) and math.isnan(z)) else None})
    fd = pd.DataFrame(rows).sort_values("Katkı (w·z)", ascending=False)
    st.markdown("**Faktör dökümü** (skoru yukarı/aşağı çeken etkenler)")
    st.bar_chart(fd.set_index("Faktör")["Katkı (w·z)"])
    st.dataframe(fd, hide_index=True, use_container_width=True)
    h = snaps[snaps["ticker"] == t].sort_values("tarih")
    if len(h) > 1:
        st.markdown("**Aylık skor geçmişi (yüzdelik)**")
        st.line_chart(h.set_index("tarih")["composite_pct"])
    lab_cols = [c for c in ["tarih", "composite_pct", "fwd_3m", "fwd_ret", "real_ret", "xu_excess"] if c in h]
    if "fwd_3m" in h and h["fwd_3m"].notna().any():
        st.markdown("**Geçmiş kararların gerçekleşen sonuçları** (fwd_ret = 12A nominal, real_ret = 12A reel)")
        st.dataframe(h[lab_cols].dropna(subset=["fwd_3m"]).tail(24), hide_index=True, use_container_width=True)


if not snaps.empty:
    all_t = sorted(set(snaps[snaps["tarih"] == snaps["tarih"].max()]["ticker"].astype(str)) | set((pf.get("positions") or {}).keys()))
    q = st.text_input("🔎 Hisse ara", placeholder="örn. THYAO, ASELS …").strip().upper()
    if q:
        hits = [t for t in all_t if q in t]
        if not hits:
            st.warning(f"'{q}' son aylık taramada yok.")
        else:
            sel = hits[0] if len(hits) == 1 or q in hits else st.selectbox("Eşleşen hisseler", hits)
            with st.container(border=True):
                render_stock(q if q in hits else sel)

t1, t2, t3, t4, t5 = st.tabs(["📌 Portföy", "💎 Aylık seçim", "📈 Performans", "🧠 Model", "📚 Backtest"])

with t1:
    pos = pf.get("positions", {}) or {}
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
        st.info("Henüz pozisyon yok. Aylık gözden geçirmenin emirleri bir sonraki seansın açılışında gerçekleşir.")
    if pf.get("pending"):
        st.subheader("Yarın açılışta çalışacak emirler")
        st.dataframe(pd.DataFrame(pf["pending"]), hide_index=True, use_container_width=True)

with t2:
    if snaps.empty:
        st.info("Aylık kesit yok.")
    else:
        last = snaps[snaps["tarih"] == snaps["tarih"].max()]
        st.caption(f"Son aylık gözden geçirme: {snaps['tarih'].max()} | alım eşiği p{cal.get('pct_cutoff')} | tutma eşiği p{C.HOLD_PCT:.0f}")
        only_sel = st.toggle("Yalnızca seçilenler", value=False)
        view = last[last["selected"].astype(str).str.lower() == "true"] if only_sel and "selected" in last else last
        cols = [c for c in ["ticker", "sector", "composite_pct", "exp_real_12m", "p_beat_cpi", "vol_ann_pct",
                            "f_roe", "f_earnings_yield", "f_book_yield", "f_mom_12_1", "f_high_52w", "selected"] if c in view]
        st.dataframe(view.sort_values("composite", ascending=False)[cols].head(60), hide_index=True, use_container_width=True)

with t3:
    if len(nav) >= 2:
        n = nav.copy()
        n["tarih"] = pd.to_datetime(n["tarih"])
        n = n.set_index("tarih")
        chart = pd.DataFrame({"Portföy": n["nav"] / n["nav"].iloc[0]})
        if "xu100" in n and n["xu100"].notna().any():
            x = n["xu100"].ffill().bfill()
            chart["XU100"] = x / x.iloc[0]
        st.line_chart(chart)
    else:
        st.info("Performans grafiği için en az iki işlem günü gerekiyor.")
    st.json(navm)
    st.subheader("Kapanan pozisyonlar")
    st.json(perf.get("lots", {}))
    if not trades.empty:
        st.dataframe(trades.tail(50), hide_index=True, use_container_width=True)

with t4:
    if weights:
        st.subheader("Faktör ağırlıkları")
        st.bar_chart(pd.Series(weights).sort_values())
    st.json({"status": m.get("status"), "ic_live_12m": m.get("ic_live_12m"), "ic_live_3m": m.get("ic_live_3m"),
             "calibration_status": cal.get("status"), "bucket_table": cal.get("bucket_table"),
             "market": cal.get("market"), "regime": reg.get("probs"), "inflation": inf})

with t5:
    if not report:
        st.info("V3 backtest raporu yok.")
    else:
        cs = (report.get("cpi_source") or {}).get("status") or (report.get("cpi_source") or {}).get("source")
        st.caption(f"{report.get('period')} | TÜFE kaynağı: {cs} | temel veri kapsamı {report.get('fundamentals_coverage')}")
        if not any((v or {}).get("cpi_pct") is not None for v in (report.get("per_year") or {}).values()):
            st.warning("Bu backtest TÜFE verisi olmadan çalışmış; reel sütunlar boş. EVDS anahtarını ekleyip backtest'i yeniden çalıştırın.")
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
