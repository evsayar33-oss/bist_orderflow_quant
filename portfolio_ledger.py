"""V2 compatibility shim — V3 portfolio accounting lives in portfolio.py (monthly, long horizon)."""
from portfolio import apply_day, new_portfolio, nav, weights  # noqa: F401
