"""BIST Reel Getiri — sade, mobil öncelikli pano (salt okunur, ./data dosyalarını okur)."""
import json
import math
import os

import pandas as pd
import streamlit as st

import config as C

st.set_page_config(page_title="BIST Reel Getiri", layout="centered", page_icon="🏛️", initial_sidebar_state="collapsed")

# ------------------------------------------------------------------ style
st.markdown("""
<style>
#MainMenu, footer, header [data-testid="stToolbar"] {visibility: hidden;}
.block-container {padding-top: 1.2rem; padding-bottom: 3rem; max-width: 760px;}
.hdr {font-size: 1.55rem; font-weight: 700; letter-spacing: -.02em; margin-bottom: .1rem;}
.sub {opacity: .65; font-size: .85rem; margin-bottom: 1rem;}
.card {border: 1px solid rgba(128,128,128,.22); border-radius: 16px; padding: 14px 16px; margin: 8px 0;
       background: rgba(128,128,128,.06);}
.big {font-size: 2.1rem; font-weight: 700; line-height: 1.1; letter-spacing: -.02em;}
.lbl {font-size: .78rem; opacity: .65; text-transform: uppercase; letter-spacing: .04em;}
.chip {display: inline-block; padding: 3px 10px; margin: 4px 6px 0 0; border-radius: 999px; font-size: .82rem;
       border: 1px solid rgba(128,128,128,.3);}
.chip.on {border-color: #f5a524; background: rgba(245,165,36,.14); font-weight: 600;}
.kpi {text-align: left;}
.kpi .v, .v {font-size: 1.35rem; font-weight: 700;}
.pos {color: #17c964;} .neg {color: #f31260;} .mut {opacity: .6;}
.row {display: flex; justify-content: space-between; align-items: center; padding: 10px 0;
      border-bottom: 1px solid rgba(128,128,128,.15);}
.row:last-child {border-bottom: none;}
.tk {font-weight: 700; font-size: 1.02rem;}
.bar {height: 6px; border-radius: 3px; background: rgba(128,128,128,.18); margin-top: 5px; width: 120px;}
.bar > div {height: 6px; border-radius: 3px; background: #7c6cf2;}
.badge {display: inline-block; padding: 4px 12px; border-radius: 10px; font-weight: 600; font-size: .9rem;}
.b-in {background: rgba(23,201,100,.15); color: #17c964;} .b-buy {background: rgba(124,108,242,.18); color: #9d90ff;}
.b-watch {background: rgba(128,128,128,.15);} .b-no {background: rgba(243,18,96,.13); color: #f31260;}
.note {font-size: .88rem; line-height: 1.45;}
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=120)
def _json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=120)
def _csv(path):
    return pd.read_csv(path, low_memory=False) if os.path.exists(path) else pd.DataFrame()


def num(v):
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def pct(v, sign=False, nd=1):
    x = num(v)
    if x is None:
        return "—"
    s = f"{abs(x):.{nd}f}".replace(".", ",")
    return (("+" if x >= 0 else "−") if sign else ("−" if x < 0 else "")) + "%" + s


def cls(v):
    x = num(v)
    return "mut" if x is None else ("pos" if x >= 0 else "neg")


AYLAR = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
BENCH = {"cpi": "TÜFE", "usd": "Dolar", "gold": "Altın", "deposit": "Mevduat", "xu100": "BIST100"}
REGIME = {"RISK_ON": "Olumlu", "NEUTRAL": "Nötr", "RISK_OFF": "Riskli"}
GUARD = {"NORMAL": "Normal", "WATCH": "Temkinli", "RECOVERY": "Toparlanıyor", "SAFE": "Güvenli mod"}
FACTOR_TR = {
    "mom_12_1": "12 aylık momentum", "high_52w": "52 hafta zirvesine yakınlık", "trend_consistency": "istikrarlı yükseliş",
    "low_vol": "düşük oynaklık", "low_beta": "piyasadan bağımsızlık", "dd_resilience": "düşüşlere dayanıklılık",
    "liquidity": "likidite", "roe": "özsermaye kârlılığı", "earnings_yield": "kâra göre ucuzluk",
    "book_yield": "defter değerine göre ucuzluk", "sales_yield": "satışlara göre ucuzluk", "op_margin": "faaliyet marjı",
    "low_leverage": "düşük borç", "div_yield": "temettü", "real_growth": "enflasyon üstü büyüme",
}
REASON_TR = {"RANK_EXIT": "skor düştü", "FUND_BREAK": "temel bozulma", "DRAWDOWN_CONFIRMED": "düşüş + zayıflayan tez",
             "CATASTROPHE_STOP": "kesin stop", "NEW_ENTRY": "yeni giriş"}


def dstr(s):
    try:
        d = pd.Timestamp(s)
        return f"{d.day} {AYLAR[d.month - 1]} {d.year}"
    except Exception:
        return "—"


state = _json(C.STATE_FILE)
report = _json(C.BACKTEST_REPORT_FILE)
nav = _csv(C.NAV_FILE)
snaps = _csv(C.MONTHLY_SNAPSHOT_FILE)

st.markdown('<div class="hdr">🏛️ BIST Reel Getiri</div>', unsafe_allow_html=True)
if not state:
    st.markdown('<div class="sub">Henüz veri yok. GitHub Actions → önce “Walk Forward Backtest”, sonra “Daily Run”.</div>',
                unsafe_allow_html=True)
    st.stop()

pf = state.get("portfolio") or {}
reg, g = state.get("regime", {}) or {}, state.get("autonomy_guard", {}) or {}
hz = state.get("hurdles", {}) or {}
perf = state.get("performance", {}) or {}
navm = perf.get("nav", {}) or {}
lr = state.get("last_run", {}) or {}
weights = (state.get("model", {}) or {}).get("champion_weights", {}) or {}
cal = state.get("calibration", {}) or {}
last = snaps[snaps["tarih"] == snaps["tarih"].max()] if not snaps.empty else pd.DataFrame()

st.markdown(f'<div class="sub">Güncelleme {dstr(lr.get("date"))} · Piyasa {REGIME.get(reg.get("label"), "—")} · '
            f'Sistem {GUARD.get(g.get("mode"), "—")}</div>', unsafe_allow_html=True)

# ------------------------------------------------------------------ one actionable alert at most
inf = state.get("inflation", {}) or {}
if inf.get("expected_12m_pct") is None:
    st.error("TÜFE verisi yok → yeni alım yapılmıyor. GitHub → Settings → Secrets → `EVDS_API_KEY` ekleyip “Daily Run” çalıştırın.")
elif inf.get("status") == "PROXY":
    st.warning("Resmî TÜFE yerine USDTRY vekili kullanılıyor. `EVDS_API_KEY` ekleyin.")

# ------------------------------------------------------------------ hurdle card
if hz.get("hurdle") is not None and hz.get("mode") == "sum":
    keys = [k for k in ("cpi", "usd", "gold", "deposit") if hz.get(k) is not None]
    chips = '<span class="mut"> + </span>'.join(f'<span class="chip">{BENCH[k]} {pct(hz.get(k))}</span>' for k in keys)
    edge = hz.get("edge", C.MIN_EDGE_OVER_HURDLE_PCT)
    chips += f'<span class="mut"> + </span><span class="chip on">Fark %{edge:.0f}</span>'
    st.markdown(f'<div class="card"><div class="lbl">Portföyün 12 aylık hedefi</div>'
                f'<div class="big">{pct(hz["hurdle"])}</div>{chips}'
                f'<div class="note mut" style="margin-top:8px">Hedef: TÜFE, dolar (+ABD enflasyonu), altın ve mevduatın '
                f'<b>toplamını</b> en az %{edge:.0f} farkla geçmek. Hiçbir hisse alınmadan önce en güçlü tek alternatifi '
                f'({BENCH.get(hz.get("binding"), "—")} {pct(hz.get("floor"))}) de güvenle geçmesi beklenir.</div></div>',
                unsafe_allow_html=True)
elif hz.get("hurdle") is not None:
    chips = "".join(f'<span class="chip {"on" if k == hz.get("binding") else ""}">{BENCH[k]} {pct(hz.get(k))}</span>'
                    for k in ("cpi", "usd", "gold", "deposit") if hz.get(k) is not None)
    st.markdown(f'<div class="card"><div class="lbl">Hisselerin geçmesi gereken 12 aylık çıta</div>'
                f'<div class="big">{pct(hz["hurdle"])}</div>{chips}'
                f'<div class="note mut" style="margin-top:8px">Seçilen her hissenin 12 ayda TÜFE, dolar (+ABD enflasyonu), '
                f'altın ve mevduatın hepsini en az %{C.MIN_EDGE_OVER_HURDLE_PCT:.0f} farkla geçmesi beklenir.</div></div>',
                unsafe_allow_html=True)

# ------------------------------------------------------------------ KPIs
bt = (navm.get("benchmarks_total_pct") or {})
if navm.get("days", 0) >= 2:
    kp = "".join(f'<div style="flex:1;min-width:0"><div class="lbl">{lbl}</div><div class="kpi v {cls(v)}">{pct(v, True)}</div></div>'
                 for lbl, v in (("Portföy", navm.get("total_return_pct")), ("Reel", navm.get("real_total_pct")),
                                ("BIST100", bt.get("xu100", navm.get("xu100_total_pct")))))
    st.markdown(f'<div class="card"><div class="lbl" style="margin-bottom:6px">Başlangıçtan bu yana</div>'
                f'<div style="display:flex;gap:12px">{kp}</div></div>', unsafe_allow_html=True)
else:
    st.markdown(f'<div class="card note mut">Portföy {dstr(pf.get("start_date"))} tarihinde başladı. '
                'Getiri kartları ilk işlem günlerinden sonra görünür.</div>', unsafe_allow_html=True)

tab1, tab2, tab3 = st.tabs(["Portföy", "Hisse Ara", "Performans"])

# ------------------------------------------------------------------ PORTFÖY
OVERLAY = {"none": "hep hisse", "trend": "trend filtresi", "dual": "hisse / altın / mevduat rotasyonu",
           "blend": "altın güçlüyse yarısı altın", "core25": "kalıcı %25 altın"}
WEIGHT = {"equal": "eşit ağırlık", "conviction": "skora göre ağırlık", "inv_vol": "risk dengeli ağırlık"}
with tab1:
    stg = next((v for v in (state.get("active_strategy"), state.get("strategy")) if isinstance(v, dict) and v), {})
    if not stg:
        try:
            with open(C.STRATEGY_CONFIG_FILE, encoding="utf-8") as fh:
                _cfg = json.load(fh)
            stg = _cfg.get("strategy") if isinstance(_cfg, dict) and isinstance(_cfg.get("strategy"), dict) else {}
        except Exception:
            stg = {}
    if stg:
        eqf, gw = num(stg.get("equity_frac")), num(stg.get("gold_w")) or 0
        alloc = ""
        if eqf is not None and eqf <= 0 and gw > 0:
            alloc = '<div class="note" style="margin-top:6px">🪙 Şu an hisse yerine <b>altın</b> tutuluyor (gram altın / ALTINS1).</div>'
        elif eqf is not None and eqf <= 0:
            alloc = '<div class="note" style="margin-top:6px">🏦 Şu an hisse yerine <b>mevduat / para piyasası</b>.</div>'
        elif eqf is not None and eqf < 1:
            alloc = (f'<div class="note" style="margin-top:6px">🪙 Hisse %{eqf * 100:.0f} · altın %{gw * 100:.0f} (gram altın / ALTINS1).</div>' if gw
                     else f'<div class="note" style="margin-top:6px">🟡 Hisse payı {pct(eqf * 100, nd=0)}, kalanı mevduatta.</div>')
        st.markdown(f'<div class="card"><div class="lbl">Strateji</div><div class="note">'
                    f'<b>{stg.get("n_positions")} hisse</b> · {WEIGHT.get(stg.get("weighting"), "risk dengeli ağırlık")} · '
                    f'{OVERLAY.get(stg.get("overlay"), stg.get("overlay"))}</div>{alloc}</div>', unsafe_allow_html=True)
    pos = pf.get("positions", {}) or {}
    navv = pf.get("nav", 1.0) or 1.0
    if pos:
        rows = ""
        for t, p in sorted(pos.items(), key=lambda kv: -kv[1]["value"]):
            w = p["value"] / navv * 100
            r = (p["level"] - 1) * 100
            flag = ' <span class="chip">⚠️ düşüş bayrağı</span>' if p.get("dd_flag") else ""
            rows += (f'<div class="row"><div><span class="tk">{t}</span>{flag}<div class="mut" style="font-size:.8rem">'
                     f'{dstr(p["entry_date"])} alındı</div></div><div style="text-align:right">'
                     f'<div class="{cls(r)}" style="font-weight:700">{pct(r, True)}</div>'
                     f'<div class="bar"><div style="width:{min(w / C.MAX_POSITION_W, 100):.0f}%"></div></div>'
                     f'<div class="mut" style="font-size:.78rem">ağırlık {pct(w, nd=0)}</div></div></div>')
        st.markdown(f'<div class="card"><div class="lbl">{len(pos)} hisse · nakit {pct(pf.get("cash", 0) / navv * 100, nd=0)}</div>'
                    f'{rows}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="card note mut">Henüz pozisyon yok. Aylık seçimdeki emirler bir sonraki seans açılışında gerçekleşir.</div>',
                    unsafe_allow_html=True)
    pend = pf.get("pending", []) or []
    if pend:
        rows = ""
        for o in pend:
            if o["action"] == "BUY":
                pb = num(o.get("entry_p_beat_all"))
                extra = f' · hedefi geçme {pct(pb * 100, nd=0)}' if pb is not None else ""
                rows += (f'<div class="row"><div><span class="tk">🟢 {o["ticker"]}</span><div class="mut" style="font-size:.8rem">'
                         f'beklenti {pct(o.get("entry_exp_nominal"))}{extra}</div></div>'
                         f'<div style="font-weight:600">{pct(o.get("target_w", 0) * 100, nd=0)}</div></div>')
            elif o["action"] == "SELL":
                rows += (f'<div class="row"><div><span class="tk">🔴 {o["ticker"]}</span></div>'
                         f'<div class="mut">{REASON_TR.get(o.get("reason"), o.get("reason"))}</div></div>')
        if rows:
            st.markdown(f'<div class="card"><div class="lbl">Bir sonraki açılışta</div>{rows}</div>', unsafe_allow_html=True)
    if not last.empty and "selected" in last:
        sel = last[last["selected"].astype(str).str.lower() == "true"].sort_values("composite", ascending=False)
        if len(sel):
            st.caption(f"Bu ayın seçimi ({dstr(snaps['tarih'].max())}): " + " · ".join(sel["ticker"].astype(str)))

# ------------------------------------------------------------------ HİSSE ARA
with tab2:
    if last.empty:
        st.info("Henüz aylık tarama yok.")
    else:
        q = st.text_input("Hisse kodu", placeholder="örn. THYAO").strip().upper()
        all_t = sorted(set(last["ticker"].astype(str)) | set((pf.get("positions") or {}).keys()))
        if q:
            hits = [t for t in all_t if q in t]
            if not hits:
                st.markdown(f'<div class="card note">“{q}” bu ayın taramasında yok (likidite ya da fiyat geçmişi yetersiz).</div>',
                            unsafe_allow_html=True)
            else:
                t = q if q in hits else (hits[0] if len(hits) == 1 else st.selectbox("Eşleşenler", hits))
                row = last[last["ticker"] == t]
                in_pf = t in (pf.get("positions") or {})
                pending_buy = any(o["ticker"] == t and o["action"] == "BUY" for o in (pf.get("pending") or []))
                if row.empty:
                    st.markdown(f'<div class="card"><span class="tk">{t}</span> — bu ay puanlanmadı.</div>', unsafe_allow_html=True)
                else:
                    r = row.iloc[0]
                    cutoff = float(cal.get("pct_cutoff", C.BUY_PCT))
                    sc = num(r.get("composite_pct")) or 0
                    if in_pf:
                        badge, txt = "b-in", "✅ Portföyde"
                    elif pending_buy or str(r.get("selected")).lower() == "true":
                        badge, txt = "b-buy", "🟢 Alım listesinde"
                    elif sc >= C.HOLD_PCT:
                        badge, txt = "b-watch", "⚪ İzlemede"
                    else:
                        badge, txt = "b-no", "🔴 Şu an uygun değil"
                    why = []
                    eo = num(r.get("exp_over_hurdle"))
                    if not in_pf and not pending_buy:
                        if sc < cutoff:
                            why.append(f"Skoru alım eşiğinin altında (100 üzerinden {sc:.0f}, eşik {cutoff:.0f}).")
                        if eo is not None and eo < C.MIN_EDGE_OVER_HURDLE_PCT:
                            why.append("Beklenen getirisi 12 aylık hedefin altında." if hz.get("mode") == "sum"
                                       else "Beklenen getirisi 12 aylık çıtayı yeterli farkla geçmiyor.")
                        eof = num(r.get("exp_over_floor"))
                        if hz.get("mode") == "sum" and eof is not None and eof < C.MIN_EDGE_OVER_HURDLE_PCT:
                            why.append("En güçlü tek alternatifi bile (" + BENCH.get(hz.get("binding"), "—") + ") güvenle geçmiyor.")
                        if (num(r.get("med_value_traded")) or 0) < C.MIN_MEDIAN_VALUE_TRADED_TL:
                            why.append("İşlem hacmi (likidite) düşük.")
                        if str(r.get("fund_break")).lower() == "true":
                            why.append("Şirket zarar ediyor ve özsermaye kârlılığı negatif.")
                        if not why:
                            why.append("Kriterleri geçiyor ama sektör sınırı ya da portföy doluluğu nedeniyle alınmadı.")
                    contrib = sorted(((k, weights.get(k, 0) * (num(r.get(f"z_{k}")) or 0)) for k in C.FACTORS),
                                     key=lambda x: x[1], reverse=True)
                    good = [FACTOR_TR[k] for k, v in contrib if v > 0.02][:3]
                    bad = [FACTOR_TR[k] for k, v in contrib[::-1] if v < -0.02][:3]
                    pb = num(r.get("p_beat_all"))
                    html = (f'<div class="card"><div style="display:flex;justify-content:space-between;align-items:center">'
                            f'<span class="big" style="font-size:1.6rem">{t}</span><span class="badge {badge}">{txt}</span></div>'
                            f'<div class="mut" style="font-size:.82rem;margin-top:2px">{r.get("sector") if isinstance(r.get("sector"), str) else ""}</div>'
                            f'<div style="display:flex;gap:18px;margin-top:12px;flex-wrap:wrap">'
                            f'<div><div class="lbl">Skor</div><div class="kpi v">{sc:.0f}<span class="mut" style="font-size:.9rem">/100</span></div></div>'
                            f'<div><div class="lbl">Beklenen 12A</div><div class="kpi v">{pct(r.get("exp_nominal_12m"))}</div></div>'
                            f'<div><div class="lbl">Hedef</div><div class="kpi v">{pct(r.get("hurdle_12m"))}</div></div>'
                            f'<div><div class="lbl">Hedefi geçme</div><div class="kpi v">{pct(pb * 100, nd=0) if pb is not None else "—"}</div></div>'
                            f'</div>')
                    if why:
                        html += '<div class="note" style="margin-top:12px">' + "<br>".join("• " + w for w in why) + "</div>"
                    if good:
                        html += f'<div class="note" style="margin-top:10px"><span class="pos">▲ Güçlü:</span> {", ".join(good)}</div>'
                    if bad:
                        html += f'<div class="note"><span class="neg">▼ Zayıf:</span> {", ".join(bad)}</div>'
                    st.markdown(html + "</div>", unsafe_allow_html=True)
                    h = snaps[snaps["ticker"] == t].sort_values("tarih")
                    if len(h) > 1:
                        st.caption("Aylık skor geçmişi")
                        st.line_chart(h.set_index("tarih")["composite_pct"], height=160)

# ------------------------------------------------------------------ PERFORMANS
with tab3:
    if len(nav) >= 2:
        n = nav.copy()
        n["tarih"] = pd.to_datetime(n["tarih"])
        n = n.set_index("tarih")
        ch = pd.DataFrame({"Portföy": n["nav"] / n["nav"].iloc[0]})
        if "xu100" in n and n["xu100"].notna().any():
            x = n["xu100"].ffill().bfill()
            ch["BIST100"] = x / x.iloc[0]
        st.line_chart(ch, height=220)
        if bt:
            rows = f'<div class="row"><span class="tk">Portföy</span><span class="{cls(navm.get("total_return_pct"))}" style="font-weight:700">{pct(navm.get("total_return_pct"), True)}</span></div>'
            for k in ("cpi", "usd", "gold", "deposit", "xu100"):
                if bt.get(k) is not None:
                    rows += f'<div class="row"><span>{BENCH[k]}</span><span class="mut">{pct(bt.get(k), True)}</span></div>'
            st.markdown(f'<div class="card"><div class="lbl">Başlangıçtan bu yana</div>{rows}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="card note mut">Canlı performans grafiği en az iki işlem gününden sonra görünür.</div>',
                    unsafe_allow_html=True)
    if report:
        p = report.get("portfolio", {}) or {}
        rb = p.get("rolling12m_beat") or {}
        summ = report.get("hurdle_mode") == "sum"
        lines = [f'Test dönemi {p.get("start", "")[:4]}–{p.get("end", "")[:4]} · yıllık <b>{pct(p.get("cagr_pct"))}</b> '
                 f'(BIST100 {pct(p.get("xu100_cagr_pct"))}) · maks. düşüş {pct(p.get("max_drawdown_pct"))}']
        if rb:
            lines.append("12 aylık dönemlerde geçme oranı: " +
                         " · ".join(f'{BENCH.get(k, {"all": "Toplam hedef" if summ else "Hepsi", "each": "Hepsi tek tek"}.get(k, k))} <b>{pct(v, nd=0)}</b>'
                                    for k, v in rb.items()))
            if summ and p.get("rolling12m_median_gap_pp") is not None:
                lines.append(f'Tipik 12 ayda toplam hedefe uzaklık: <b>{pct(p.get("rolling12m_median_gap_pp"), True)}</b> puan')
        if report.get("universe_downloaded"):
            lines.append(f'Test evreni: <b>{report.get("universe_downloaded")}</b> hisse · her ay o tarihteki likidite eşiğiyle')
        elif p.get("rolling12m_beat_cpi_pct") is not None:
            lines.append(f'12 aylık dönemlerin <b>{pct(p.get("rolling12m_beat_cpi_pct"), nd=0)}</b>’inde TÜFE’yi geçti.')
        st.markdown('<div class="card"><div class="lbl">Geçmiş test (gerçek veri, dışarıda bırakılmış dönemler)</div>'
                    f'<div class="note">{"<br>".join(lines)}</div></div>', unsafe_allow_html=True)
        lab = report.get("strategy_lab") or {}
        if lab:
            sel = lab.get("selected") or {}
            adopted = lab.get("adopted")
            txt = (f'{lab.get("variants_tested")} farklı portföy kuralı gerçek veride denendi. '
                   + ("Her yıl yalnızca o yıldan ÖNCEKİ verilerle en iyi kural seçilerek test edildi; bu seçim yöntemi "
                      "varsayılan kurallardan iyi çıktığı için canlıya alındı." if adopted else
                      "Kural seçimi geçmişte varsayılan kuralları geçemediği için canlıda varsayılan kurallar kullanılıyor.")
                   + f'<br>Canlı kural: <b>{sel.get("n_positions")} hisse</b> · '
                   f'{WEIGHT.get(sel.get("weighting"), "risk dengeli")} · '
                   f'{OVERLAY.get(sel.get("overlay"), sel.get("overlay"))} · alım eşiği {sel.get("buy_pct") or "kalibre"}')
            if lab.get("meta_beat_hurdle_pct") is not None:
                txt += (f'<br>12 aylık dönemlerin <b>{pct(lab.get("meta_beat_hurdle_pct"), nd=0)}</b>’inde '
                        + ("toplam hedefi (TÜFE+dolar+altın+mevduat+%3) geçti." if summ else "çıtanın tamamını geçti."))
            st.markdown(f'<div class="card"><div class="lbl">Strateji laboratuvarı</div><div class="note">{txt}</div></div>',
                        unsafe_allow_html=True)
        py = report.get("per_year") or {}
        if py:
            tbl = []
            for y, v in py.items():
                tbl.append({"Yıl": str(y), "Portföy": pct(v.get("nominal_pct"), True), "TÜFE": pct(v.get("cpi_pct")),
                            "Dolar": pct(v.get("usd_pct")), "Altın": pct(v.get("gold_pct")), "Mevduat": pct(v.get("deposit_pct")),
                            "BIST100": pct(v.get("xu100_pct"), True),
                            **({"Hedef (toplam)": pct(v.get("hurdle_pct")),
                                "Tek tek": {True: "✅", False: "❌"}.get(v.get("beat_each"), "—")} if summ else {}),
                            ("Hedefi geçti" if summ else "Hepsini geçti"): {True: "✅", False: "❌"}.get(v.get("beat_all"), "—")})
            st.dataframe(pd.DataFrame(tbl), hide_index=True, use_container_width=True)

with st.expander("Teknik detay"):
    m = state.get("model", {}) or {}
    st.json({"model": m.get("status"), "sürüm": m.get("champion_version"), "kalibrasyon": cal.get("status"),
             "alım eşiği": cal.get("pct_cutoff"), "çıta": hz, "TÜFE": inf, "nakit": state.get("cash_rate"),
             "rejim olasılıkları": reg.get("probs"), "guard": {k: g.get(k) for k in ("mode", "reason", "drift_score", "performance_drift")},
             "canlı IC": {"12A": m.get("ic_live_12m"), "3A": m.get("ic_live_3m")}, "kapanan pozisyonlar": perf.get("lots")})
