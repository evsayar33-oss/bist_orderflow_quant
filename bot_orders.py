"""V3.14 — Bot için makine-okur emir dosyası.

Her kapanış çalışmasından sonra yazılır:
  data/orders.json          -> ertesi işlem günü AÇILIŞTA uygulanacak güncel emir listesi (boş olabilir)
  data/orders_history.jsonl -> yeni emir üretilen her günün kaydı (satır başına bir gün)

Sistem ağırlıkla çalışır (portföy değeri = 1). config.BOT_CAPITAL_TL verilirse her emre TL tutar ve
tahmini adet de eklenir (adet = tutar / son kapanış, aşağı yuvarlanır; BIST'te kesirli hisse yoktur).

Emir alanları:
  id            benzersiz kimlik (aynı emri iki kez uygulamamak için)
  action        BUY | SELL | REBALANCE
                BUY: yeni pozisyon, target_weight kadar
                SELL: pozisyonun TAMAMINI sat (sell_all=true)
                REBALANCE: pozisyonu target_weight'e getir (delta_weight > 0 ise al, < 0 ise sat)
  symbol / yahoo_symbol   THYAO / THYAO.IS
  target_weight, current_weight, delta_weight   portföy ağırlıkları (0-1)
  amount_tl, quantity     (yalnızca BOT_CAPITAL_TL verildiyse) işlem tutarı ve tahmini adet; işaret yön verir
  ref_price      son kapanış (bilgi amaçlı, limit değil)
  order_type     MARKET_ON_OPEN
  execute_date   uygulanacak gün (bir sonraki BIST seansı)
  exit_month     planlanan çıkış ayı (BUY için)
  reason         sistemin gerekçe kodu (NEW_ENTRY, COHORT_WEIGHT, CATASTROPHE_STOP, NOT_SELECTED ...)
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

import calendar_tr as cal
import config as C


def _weights(pf):
    nav = float(pf.get("nav") or 0) or 1.0
    return {t: float(p.get("value", 0)) / nav for t, p in (pf.get("positions") or {}).items()}


def build(state, today, prices=None):
    pf = state.get("portfolio") or {}
    pend = pf.get("pending") or []
    w = _weights(pf)
    exe = cal.add_sessions(pd.Timestamp(today), 1)
    cap = getattr(C, "BOT_CAPITAL_TL", None)
    cap_now = float(cap) * float(pf.get("nav") or 1.0) if cap else None
    lr = state.get("last_rebalance") or {}
    exit_m = lr.get("exit_month") or {}
    prices = prices or {}
    orders = []
    for o in pend:
        t = str(o.get("ticker"))
        act = str(o.get("action", "")).upper()
        cur = round(w.get(t, 0.0), 6)
        if act == "SELL":
            action, tgt = "SELL", 0.0
        elif act == "BUY":
            action, tgt = "BUY", float(o.get("target_w") or 0)
        else:
            action, tgt = "REBALANCE", float(o.get("target_w") or 0)
        delta = round(tgt - cur, 6)
        if action == "REBALANCE" and abs(delta) < 0.005:
            continue                                  # %0,5'ten küçük ayar: gürültü, bota gönderilmez
        px = prices.get(t) or (pf.get("positions", {}).get(t, {}) or {}).get("last_close")
        rec = {"id": f"bist-{pd.Timestamp(today).date()}-{t}-{action}", "action": action, "symbol": t,
               "yahoo_symbol": f"{t}.IS", "target_weight": round(tgt, 6), "current_weight": cur,
               "delta_weight": delta, "sell_all": action == "SELL", "ref_price": float(px) if px else None,
               "order_type": "MARKET_ON_OPEN", "execute_date": str(pd.Timestamp(exe).date()),
               "exit_month": exit_m.get(t), "reason": o.get("reason")}
        if cap_now and px:
            amt = (-cur if action == "SELL" else delta) * cap_now
            rec["amount_tl"] = round(amt, 2)
            q = abs(amt) / float(px)
            rec["quantity"] = int(np.floor(q)) * (1 if amt >= 0 else -1)
        orders.append(rec)
    positions = [{"symbol": t, "weight": round(w.get(t, 0), 6), "entry_date": p.get("entry_date"),
                  "entry_price": p.get("entry_px"), "last_price": p.get("last_close"),
                  "return_pct": round((float(p.get("level", 1)) - 1) * 100, 2),
                  "exit_month": exit_m.get(t)} for t, p in (pf.get("positions") or {}).items()]
    return {"schema": "boq.orders.v1", "engine_version": C.ENGINE_VERSION,
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "signal_date": str(pd.Timestamp(today).date()), "execute_date": str(pd.Timestamp(exe).date()),
            "currency": "TRY", "capital_tl": cap_now, "cash_weight": round(float(pf.get("cash", 0)) / (float(pf.get("nav") or 1)), 6),
            "orders": orders, "positions": positions}


def write(doc):
    os.makedirs(C.DATA_DIR, exist_ok=True)
    prev_ids = set()
    if os.path.exists(C.ORDERS_FILE):
        try:
            with open(C.ORDERS_FILE, encoding="utf-8") as f:
                prev_ids = {o["id"] for o in json.load(f).get("orders", [])}
        except Exception:
            pass
    tmp = C.ORDERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, default=float)
    os.replace(tmp, C.ORDERS_FILE)
    new = [o for o in doc["orders"] if o["id"] not in prev_ids]
    if new:
        with open(C.ORDERS_HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"signal_date": doc["signal_date"], "execute_date": doc["execute_date"], "orders": new},
                               ensure_ascii=False, default=float) + "\n")
    return new


def telegram_block(new_orders):
    """Yeni emirler için sabit biçimli satırlar (bot da okuyabilir)."""
    if not new_orders:
        return None
    from html import escape
    L = []
    for o in new_orders:
        s = f"{o['action']} {o['yahoo_symbol']} W={o['target_weight']:.4f}"
        if o["action"] == "REBALANCE":
            s += f" DW={o['delta_weight']:+.4f}"
        if o.get("quantity") is not None:
            s += f" QTY={o['quantity']} AMT={o['amount_tl']}"
        s += f" MARKET_ON_OPEN {o['execute_date']}"
        L.append(s)
    return "🤖 <b>Bot emirleri</b> (data/orders.json)\n<pre>" + escape("\n".join(L[:60])) + "</pre>"
