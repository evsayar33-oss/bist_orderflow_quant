"""Weekly / on-demand audit report (read-only: never changes model state).

Summarises the live, out-of-sample evidence: realised trades from the ledger,
the recorded composite's IC, per-factor live IC vs. the research prior,
calibration and guard status, then sends it to Telegram.
The daily learning/ledger work happens in main.py after the close.
"""
from __future__ import annotations

from html import escape

import numpy as np
import pandas as pd

import config as C
from learner_engine import ZCOLS, daily_rank_ic, newey_west
from main import build_dataset, send_telegram
from portfolio_ledger import performance_summary
from state_manager import load_ledger, load_research_prior, load_snapshots, load_state


def audit() -> str:
    state, ledger, snaps, research = load_state(), load_ledger(), load_snapshots(), load_research_prior()
    perf = performance_summary(ledger)
    ds = build_dataset(snaps)
    lines = ["🧪 <b>BIST META-ENGINE V2 — HAFTALIK DENETİM</b>"]
    lines.append(f"Snapshot seansı: {snaps['tarih'].nunique() if not snaps.empty else 0} | çözülmüş etiketli satır: {len(ds)}")
    if perf.get("closed"):
        lines.append(f"💼 Kapanan {perf['closed']} | isabet %{perf['hit_rate_pct']} | ort. net %{perf['avg_net_pct']} | "
                     f"PF {perf.get('profit_factor')} | tarih-kümeli ort. %{perf['date_clustered_mean']} (LCB90 {perf['date_clustered_lcb90']})")
        lines.append(f"Çıkış dağılımı: {perf.get('exit_mix')}")
    else:
        lines.append("💼 Henüz kapanmış işlem yok.")
    li = state.get("model", {}).get("composite_ic_live", {})
    if li.get("n_dates"):
        lines.append(f"📊 Canlı composite IC: {li['ic_mean']:+.4f} (t={li['t_nw']:.2f}, n={li['n_dates']}, son20 {li.get('ic_recent20')})")
    if not ds.empty:
        ic = daily_rank_ic(ds.dropna(subset=["fwd_ret"]), ZCOLS, "fwd_ret")
        if len(ic) >= 5:
            lines.append("🔬 <b>Faktör IC (canlı | araştırma)</b>")
            for k in C.FACTORS:
                m, _, t, n = newey_west(ic[f"z_{k}"], C.LABEL_HORIZON - 1)
                pr = (research or {}).get("ic_mean", {}).get(k)
                lines.append(f"• {k}: {m:+.4f} (t={t:.2f}) | {pr if pr is not None else '-'}")
    m = state.get("model", {})
    lines.append(f"🧠 Model: {escape(str(m.get('champion_version')))} | {escape(str(m.get('status')))} | "
                 f"terfi {m.get('promotions', 0)} / geri alma {m.get('rollbacks', 0)}")
    w = m.get("champion_weights", {})
    if w:
        lines.append("⚖️ Ağırlıklar: " + ", ".join(f"{k} {v:+.2f}" for k, v in sorted(w.items(), key=lambda x: -abs(x[1]))))
    cal = state.get("calibration", {})
    lines.append(f"🎯 Kalibrasyon: {cal.get('status')} ({cal.get('source')}) | eşik p{cal.get('pct_cutoff')}")
    g = state.get("autonomy_guard", {})
    lines.append(f"🛡️ Guard: {g.get('mode')} | {escape(str(g.get('reason')))}")
    tk = state.get("takas", {})
    lines.append(f"🏦 Takas kapsaması: %{float(tk.get('last_coverage', 0)) * 100:.0f} ({'aktif' if tk.get('enabled') else 'devre dışı'})")
    if research:
        lines.append(f"📚 Araştırma prior: {research.get('generated_at')} | {research.get('n_dates')} seans")
    else:
        lines.append("📚 Araştırma prior YOK → Actions'ta 'Walk Forward Backtest' iş akışını bir kez elle çalıştırın.")
    return "\n".join(lines)


if __name__ == "__main__":
    msg = audit()
    send_telegram(msg)   # prints to the log when Telegram secrets are absent
