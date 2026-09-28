"""Weekly audit report for V3 (read-only). Real (CPI) and XU100 yardsticks."""
from __future__ import annotations

from html import escape

import pandas as pd

import config as C
import inflation as INF
from backtest_validator import enrich_lots, lot_metrics, nav_metrics
from main import send_telegram
from state_manager import load_monthly_snapshots, load_nav, load_research_prior, load_state, load_trade_log


def audit() -> str:
    st, prior = load_state(), load_research_prior()
    cpi, cmeta = INF.load_cpi()
    nav = load_nav()
    idx = None
    if not nav.empty and "xu100" in nav:
        idx = pd.Series(pd.to_numeric(nav["xu100"], errors="coerce").to_numpy(), index=pd.to_datetime(nav["tarih"])).dropna()
    nm = nav_metrics(nav, cpi)
    lm = lot_metrics(enrich_lots(load_trade_log(), cpi, idx))
    snaps = load_monthly_snapshots()
    pf = st.get("portfolio") or {}
    L = ["🧪 <b>BIST REEL GETİRİ MOTORU V3 — HAFTALIK DENETİM</b>",
         f"TÜFE: yıllık %{st.get('inflation', {}).get('yoy_pct')} | kaynak {cmeta.get('source')} (son ay {cmeta.get('last_month')})"]
    if nm.get("days"):
        L.append(f"📊 Portföy {nm['start']}→{nm['end']}: nominal %{nm['total_return_pct']} | reel %{nm.get('real_total_pct')} "
                 f"| XU100 %{nm.get('xu100_total_pct')} | maks. düşüş %{nm['max_drawdown_pct']}")
        if nm.get("rolling12m_windows"):
            L.append(f"🎯 12 aylık pencereler: TÜFE'yi yenme %{nm['rolling12m_beat_cpi_pct']} | XU100'ü yenme %{nm['rolling12m_beat_xu100_pct']} (n={nm['rolling12m_windows']})")
    if lm.get("closed_lots"):
        L.append(f"💼 Kapanan {lm['closed_lots']} pozisyon | ort. {lm.get('avg_months_held')} ay | TÜFE'yi yenen %{lm.get('hit_beat_cpi_pct')} "
                 f"| ort. reel %{lm.get('avg_real_pct')} | çıkış {lm.get('exit_mix')}")
    if pf.get("positions"):
        L.append("📌 Açık pozisyonlar: " + ", ".join(f"{t} %{(p['level'] - 1) * 100:+.1f}" for t, p in pf["positions"].items()))
    m = st.get("model", {})
    L.append(f"🧠 Model {escape(str(m.get('champion_version')))} | {escape(str(m.get('status')))}")
    for key, name in (("ic_live_12m", "12A"), ("ic_live_3m", "3A")):
        v = m.get(key, {})
        if v.get("n_dates"):
            L.append(f"   canlı IC {name}: {v['ic_mean']:+.3f} (t={v['t_nw']}, n={v['n_dates']} ay)")
    L.append(f"🛡️ Guard {st.get('autonomy_guard', {}).get('mode')} | {escape(str(st.get('autonomy_guard', {}).get('reason')))}")
    L.append(f"🗂️ Aylık kesit sayısı: {snaps['tarih'].nunique() if not snaps.empty else 0}")
    L.append(f"📚 Araştırma prior: {prior.get('generated_at') if prior else 'YOK → Actions: Walk Forward Backtest çalıştırın'}")
    return "\n".join(L)


if __name__ == "__main__":
    send_telegram(audit())
