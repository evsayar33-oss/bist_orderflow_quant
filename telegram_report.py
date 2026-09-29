"""Clean, minimal Telegram messages (HTML parse mode). One idea per line, no jargon."""
from __future__ import annotations

from html import escape
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
REASON_TR = {"RANK_EXIT": "skor düştü", "FUND_BREAK": "temel bozulma", "DRAWDOWN_CONFIRMED": "düşüş + zayıflayan tez",
             "CATASTROPHE_STOP": "kesin stop (−%50)", "NEW_ENTRY": "yeni giriş",
             "ROTATION": "varlık rotasyonu", "ALLOCATION": "varlık dağılımı"}
OVERLAY_TR = {"none": "hep hisse", "trend": "trend filtresi", "dual": "hisse/altın/mevduat rotasyonu"}


def live_strategy(state: Dict) -> Dict:
    """Live strategy dict (V3.6 key); tolerant to old states where "strategy" is a name string."""
    for k in ("active_strategy", "strategy"):
        v = state.get(k)
        if isinstance(v, dict) and v:
            return v
    return {}


def strategy_line(state: Dict) -> Optional[str]:
    s = live_strategy(state)
    if not s:
        return None
    w = "eşit ağırlık" if s.get("weighting") == "equal" else "risk dengeli ağırlık"
    return f"⚙️ Strateji: {s.get('n_positions')} hisse · {w} · {OVERLAY_TR.get(s.get('overlay'), s.get('overlay'))}"


def allocation_line(state: Dict) -> Optional[str]:
    s = live_strategy(state)
    try:
        eq = float(s.get("equity_frac", 1.0) if s.get("equity_frac") is not None else 1.0)
        gw = float(s.get("gold_w") or 0.0)
    except (TypeError, ValueError):
        return None
    if eq >= 0.999 and not gw:
        return None
    if eq <= 0 and gw > 0:
        return "🪙 <b>Rotasyon: hisseler yerine ALTIN</b> (gram altın / ALTINS1) — altın 12 ayda hisse ve mevduattan güçlü"
    if eq <= 0:
        return "🏦 <b>Rotasyon: hisseler yerine MEVDUAT / para piyasası</b> — hisse ve altın mevduatı geçemiyor"
    return f"🟡 Trend zayıf: hisse payı %{eq * 100:.0f}, kalanı mevduatta"
REGIME_TR = {"RISK_ON": "Olumlu 🟢", "NEUTRAL": "Nötr ⚪", "RISK_OFF": "Riskli 🔴", "UNKNOWN": "Belirsiz"}
GUARD_TR = {"NORMAL": "Normal", "WATCH": "Temkinli", "RECOVERY": "Toparlanıyor", "SAFE": "Güvenli mod — alım yok"}
BENCH_TR = {"cpi": "TÜFE", "usd": "Dolar", "gold": "Altın", "deposit": "Mevduat", "xu100": "BIST100"}


def tarih(d) -> str:
    d = pd.Timestamp(d)
    return f"{d.day} {AYLAR[d.month - 1]} {d.year}"


def pct(v, sign: bool = False, nd: int = 1) -> str:
    try:
        x = float(v)
        if not np.isfinite(x):
            return "—"
    except (TypeError, ValueError):
        return "—"
    s = f"{abs(x):.{nd}f}".replace(".", ",")
    if sign:
        return ("+" if x >= 0 else "−") + "%" + s
    return ("−" if x < 0 else "") + "%" + s


def hurdle_block(h: Dict) -> List[str]:
    if not isinstance(h, dict) or h.get("hurdle") is None:
        return ["🎯 <b>Hedef hesaplanamadı</b> — TÜFE verisi yok, yeni alım yapılmadı."]
    parts = [f"{BENCH_TR[k]} {pct(h.get(k))}" for k in ("cpi", "usd", "gold", "deposit") if h.get(k) is not None]
    if h.get("mode") == "sum":
        edge = h.get("edge", 3.0)
        return [f"🎯 <b>12 aylık hedef: {pct(h['hurdle'])}</b>  <i>(hepsinin toplamı + %{edge:.0f})</i>",
                "   " + " + ".join(parts)]
    return [f"🎯 <b>12 aylık çıta: {pct(h['hurdle'])}</b>  <i>(en yüksek: {BENCH_TR.get(h.get('binding'), '?')})</i>",
            "   " + " · ".join(parts)]


def portfolio_line(state: Dict) -> List[str]:
    pf = state.get("portfolio") or {}
    nav = pf.get("nav", 1.0) or 1.0
    n = len(pf.get("positions", {}))
    cash = pf.get("cash", 0.0) / nav * 100 if nav else 0.0
    out = [f"💼 Portföy: <b>{n} hisse</b> · nakit {pct(cash, nd=0)}"]
    navm = (state.get("performance") or {}).get("nav") or {}
    if navm.get("days", 0) >= 2:
        b = navm.get("benchmarks_total_pct") or {}
        out.append(f"📈 Başlangıçtan: <b>{pct(navm.get('total_return_pct'), True)}</b> · reel {pct(navm.get('real_total_pct'), True)}"
                   f" · BIST100 {pct(b.get('xu100', navm.get('xu100_total_pct')), True)}")
    return out


def monthly_report(today, state: Dict, rev: Dict, buys: List[Dict]) -> str:
    reg, g = state.get("regime", {}), state.get("autonomy_guard", {})
    L = [f"🏛 <b>BIST Reel Getiri</b> · Aylık Rapor", f"<i>{tarih(today)} · emirler bir sonraki açılışta</i>", ""]
    L += hurdle_block(state.get("hurdles") or {})
    al = allocation_line(state)
    if al:
        L += ["", al]
    L.append("")
    tw = rev.get("target_weights", {})
    if tw.get("ALTIN"):
        L.append(f"🪙 <b>ALTIN</b>  ağırlık {pct(tw['ALTIN'] * 100, nd=0)} <i>(gram altın / ALTINS1)</i>")
    if buys:
        L.append("🟢 <b>AL</b>")
        for r in buys:
            pb = r.get("p_beat_all")
            prob = f" · hedefi geçme {pct((pb or 0) * 100, nd=0)}" if pb is not None and pb == pb else ""
            L.append(f"<b>{escape(str(r['ticker']))}</b>  ağırlık {pct(tw.get(r['ticker'], 0) * 100, nd=0)}"
                     f" · beklenti {pct(r.get('exp_nominal_12m'))}{prob}")
    if rev.get("sells"):
        L.append("")
        L.append("🔴 <b>SAT</b>")
        for t, why in rev.get("sell_reasons", {}).items():
            L.append(f"<b>{escape(str(t))}</b>  {REASON_TR.get(why, why)}")
    if rev.get("holds"):
        L.append("")
        L.append("⚪ <b>TUT</b>  " + " · ".join(escape(str(t)) for t in rev["holds"]))
    if not buys and not rev.get("sells"):
        L.append("✋ Bu ay değişiklik yok" + (" — en güçlü alternatifi güvenle geçen yeni hisse bulunamadı." if not rev.get("holds") else "."))
    L.append("")
    L += portfolio_line(state)
    sl = strategy_line(state)
    if sl:
        L.append(sl)
    L.append(f"🧭 Piyasa: {REGIME_TR.get(reg.get('label'), reg.get('label'))} · Sistem: {GUARD_TR.get(g.get('mode'), g.get('mode'))}")
    return "\n".join(L)


def events_report(today, events: List[Dict], state: Dict, day_ret: Optional[float]) -> str:
    buys = [e for e in events if e["type"] == "BUY"]
    sells = [e for e in events if e["type"].startswith("SELL_")]
    flags = [e for e in events if e["type"] == "DRAWDOWN_FLAG"]
    stops = [e for e in events if e["type"] == "HARD_STOP_QUEUED"]
    L = [f"🔔 <b>BIST Reel Getiri</b> · {tarih(today)}", ""]
    if buys:
        L.append("🟢 Alındı: " + " · ".join(f"<b>{escape(e['ticker'])}</b> {pct(e.get('w', 0) * 100, nd=0)}" for e in buys))
    for e in sells:
        why = REASON_TR.get(e["type"][5:], e["type"][5:])
        L.append(f"🔴 Satıldı: <b>{escape(e['ticker'])}</b> {pct(e.get('ret'), True)} <i>({why})</i>")
    for e in stops:
        L.append(f"⛔ Kesin stop: <b>{escape(e['ticker'])}</b> {pct(e.get('dd_entry'), True)} — yarın açılışta satılacak")
    for e in flags:
        L.append(f"⚠️ Düşüş bayrağı: <b>{escape(e['ticker'])}</b> {pct(e.get('dd_peak'), True)} zirveden — ay başında tez kontrol edilecek")
    if day_ret is not None:
        L.append("")
        L.append(f"💼 Portföy bugün {pct(day_ret, True, 2)}")
    return "\n".join(L)


def blocked_report(today, reason: str) -> str:
    return f"🛑 <b>BIST Reel Getiri</b> · {tarih(today)}\nVeri kalitesi yetersiz ({escape(reason)}). Bugün işlem yapılmadı."


def status_report(today, state: Dict, refresh: bool) -> str:
    """Short daily status — sent on every run where nothing else was sent (so a run is never silent)."""
    pf = state.get("portfolio") or {}
    reg, g = state.get("regime", {}) or {}, state.get("autonomy_guard", {}) or {}
    lr = state.get("last_rebalance") or {}
    L = [f"📋 <b>BIST Reel Getiri</b> · Günlük Durum", f"<i>{tarih(today)}</i>", ""]
    if refresh:
        L.append("⏳ Seans kapanmadı (ya da bugün seans yok): kapanış verisi 18:25 çalışmasında işlenir. "
                 "Bu çalıştırmada TÜFE, faiz, kıyaslar ve piyasa durumu güncellendi.")
        L.append("")
    L += hurdle_block(state.get("hurdles") or {})
    L.append("")
    pend = [o for o in pf.get("pending", []) if o.get("action") == "BUY"]
    psell = [o for o in pf.get("pending", []) if o.get("action") == "SELL"]
    if pend:
        L.append("🕘 <b>Bir sonraki açılışta alınacak</b>")
        L.append("   " + " · ".join(f"<b>{escape(str(o['ticker']))}</b> {pct(o.get('target_w', 0) * 100, nd=0)}" for o in pend))
    if psell:
        L.append("🕘 <b>Bir sonraki açılışta satılacak:</b> " + " · ".join(escape(str(o["ticker"])) for o in psell))
    pos = pf.get("positions", {}) or {}
    if pos:
        best = sorted(pos.items(), key=lambda kv: -kv[1].get("level", 1))
        L.append("💼 <b>Portföy</b>  " + " · ".join(f"{escape(str(t))} {pct((p.get('level', 1) - 1) * 100, True)}" for t, p in best[:12]))
    elif not pend:
        L.append("💼 Portföy boş — hedefe uygun hisse çıktığında alım yapılacak.")
    L += portfolio_line(state)[1:]
    if lr.get("date"):
        L.append(f"🔎 Son tarama: {tarih(lr['date'])} · {lr.get('n_scored', '—')} hisse puanlandı · sonraki: ayın ilk seansı")
    L.append(f"🧭 Piyasa: {REGIME_TR.get(reg.get('label'), reg.get('label'))} · Sistem: {GUARD_TR.get(g.get('mode'), g.get('mode'))}")
    return "\n".join(L)
