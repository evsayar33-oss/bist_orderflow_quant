"""Clean, minimal Telegram messages (HTML parse mode). One idea per line, no jargon."""
from __future__ import annotations

from html import escape
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
REASON_TR = {"RANK_EXIT": "skor düştü", "FUND_BREAK": "temel bozulma", "DRAWDOWN_CONFIRMED": "düşüş + zayıflayan tez",
             "CATASTROPHE_STOP": "kesin stop (−%50)", "NEW_ENTRY": "yeni giriş"}
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
    if not h or h.get("hurdle") is None:
        return ["🎯 <b>Çıta hesaplanamadı</b> — TÜFE verisi yok, yeni alım yapılmadı."]
    parts = [f"{BENCH_TR[k]} {pct(h.get(k))}" for k in ("cpi", "usd", "gold", "deposit") if h.get(k) is not None]
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
    L.append("")
    tw = rev.get("target_weights", {})
    if buys:
        L.append("🟢 <b>AL</b>")
        for r in buys:
            pb = r.get("p_beat_all")
            prob = f" · çıtayı geçme {pct((pb or 0) * 100, nd=0)}" if pb is not None and pb == pb else ""
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
        L.append("✋ Bu ay değişiklik yok" + (" — çıtayı güvenle geçen yeni hisse bulunamadı." if not rev.get("holds") else "."))
    L.append("")
    L += portfolio_line(state)
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
