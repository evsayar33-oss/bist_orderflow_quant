"""DEPRECATED in V2 (kept only so old imports do not break).

V1 optimised the T+3 win rate of a score threshold. V2 optimises the expected
NET trade return (triple-barrier, after costs) with date-clustered lower
confidence bounds: see calibration.py.
"""
from calibration import calibrate, expected_excess  # noqa: F401


def optimize_win_rate(state, data=None, **kwargs):  # pragma: no cover
    return state


def summary(state):  # pragma: no cover
    c = (state or {}).get("calibration", {})
    return f"V2 calibration: {c.get('status')} | cutoff p{c.get('pct_cutoff')}"
