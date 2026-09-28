"""V2 compatibility shim — V3 data collection lives in market_data.py."""
from market_data import fetch_snapshot, download_history  # noqa: F401
