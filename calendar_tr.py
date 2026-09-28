"""Borsa Istanbul session calendar.

Used to (1) skip runs on holidays, (2) count label horizons in real sessions
instead of "stored snapshots", and (3) detect missing snapshots.
Half-day sessions (arefe, 28 Ekim) are trading sessions and are NOT listed.
Religious holiday dates for future years are official announcements/estimates;
edit EXTRA_CLOSURES if Borsa Istanbul announces a change.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, List

import numpy as np
import pandas as pd

_FIXED = [(1, 1), (4, 23), (5, 1), (5, 19), (7, 15), (8, 30), (10, 29)]

_RELIGIOUS = [
    # 2024
    "2024-04-10", "2024-04-11", "2024-04-12",
    "2024-06-17", "2024-06-18", "2024-06-19",
    # 2025
    "2025-03-31", "2025-04-01",
    "2025-06-06", "2025-06-09",
    # 2026
    "2026-03-20",
    "2026-05-27", "2026-05-28", "2026-05-29",
    # 2027
    "2027-03-09", "2027-03-10", "2027-03-11",
    "2027-05-17", "2027-05-18", "2027-05-19",
]

EXTRA_CLOSURES: List[str] = []


def _holiday_set() -> set:
    out = set()
    for y in range(2015, 2031):
        for m, d in _FIXED:
            out.add(date(y, m, d))
    for s in _RELIGIOUS + EXTRA_CLOSURES:
        out.add(pd.Timestamp(s).date())
    return out


HOLIDAYS = _holiday_set()


def _d(x) -> date:
    return pd.Timestamp(x).date()


def is_session(x) -> bool:
    d = _d(x)
    return d.weekday() < 5 and d not in HOLIDAYS


def sessions_between(start, end) -> List[pd.Timestamp]:
    """All sessions in [start, end]."""
    s, e = _d(start), _d(end)
    out = []
    cur = s
    while cur <= e:
        if is_session(cur):
            out.append(pd.Timestamp(cur))
        cur += timedelta(days=1)
    return out


def add_sessions(x, n: int) -> pd.Timestamp:
    """The n-th session after x (n>=1)."""
    cur = _d(x)
    k = 0
    while k < n:
        cur += timedelta(days=1)
        if is_session(cur):
            k += 1
    return pd.Timestamp(cur)


def sessions_after_count(d0, d1) -> int:
    """Number of sessions in (d0, d1]."""
    a, b = _d(d0), _d(d1)
    if b <= a:
        return 0
    return len(sessions_between(a + timedelta(days=1), b))


def missing_sessions(dates: Iterable) -> List[pd.Timestamp]:
    """Sessions between the first and last stored date that have no snapshot."""
    ds = sorted({_d(x) for x in dates})
    if len(ds) < 2:
        return []
    have = set(ds)
    return [t for t in sessions_between(ds[0], ds[-1]) if t.date() not in have]


def today_tr() -> pd.Timestamp:
    return pd.Timestamp.now(tz="Europe/Istanbul").normalize().tz_localize(None)
