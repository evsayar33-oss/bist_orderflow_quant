"""Enforce persisted autonomy SAFE on already queued orders, not just new plans.

Never cancel protective SELLs or position-reducing REBALs. This module neither
places orders nor independently changes the strategy's risk parameters.
"""
from __future__ import annotations

import math

from portfolio import weights


def enforce_pending_gate(portfolio: dict, guard: dict | None) -> int:
    guard = guard or {}
    blocked = bool(guard.get("block_new_entries")) or str(guard.get("mode", "")).upper() == "SAFE"
    if not blocked:
        return 0
    current = weights(portfolio)
    retained = []
    dropped = 0
    for order in portfolio.get("pending", []):
        action = str(order.get("action", "")).upper()
        if action == "BUY":
            dropped += 1
            continue
        if action == "REBAL":
            try:
                target = float(order.get("target_w"))
            except (TypeError, ValueError):
                target = float("nan")
            if not math.isfinite(target) or target > current.get(order.get("ticker"), 0.0) + 1e-9:
                dropped += 1
                continue
        retained.append(order)
    portfolio["pending"] = retained
    return dropped
