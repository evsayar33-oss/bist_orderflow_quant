"""Historical BIST membership evidence: strict, auditable, never synthetic.

The public source is a *research lead*, not an official verified daily universe.
Snapshot dates are lower-frequency observations, NOT daily event timestamps.
No execution / strategy state is modified by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, urlopen
import json
import re

import pandas as pd

SOURCE_URL = ("https://raw.githubusercontent.com/GocmenCo/"
              "bist-constituents/master/data/bist_constituents.csv")
REQUIRED = {"snapshot_date", "ticker"}
VALID_TICKER = re.compile(r"^[A-Z0-9]{3,7}$")


def read_membership(blob: bytes) -> pd.DataFrame:
    """Parse untrusted snapshot CSV; reject bad dates/tickers/conflicts."""
    from io import BytesIO
    df = pd.read_csv(BytesIO(blob), dtype=str, keep_default_na=False)
    if not REQUIRED.issubset(df.columns):
        raise ValueError("PIT_MEMBERSHIP_SCHEMA_MISSING")
    df = df.copy()
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], errors="coerce").dt.normalize()
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    if df.empty or df["snapshot_date"].isna().any():
        raise ValueError("PIT_MEMBERSHIP_EMPTY_OR_BAD_DATE")
    if not df["ticker"].map(lambda t: bool(VALID_TICKER.fullmatch(t))).all():
        raise ValueError("PIT_MEMBERSHIP_INVALID_SYMBOL")
    # One company can carry an index-tier annotation; only date/ticker denotes
    # broad listing. Contradictory names in the same snapshot are not trusted.
    if "name" in df.columns:
        aliases = df.groupby(["snapshot_date", "ticker"])["name"].nunique()
        if (aliases > 1).any():
            raise ValueError("PIT_MEMBERSHIP_CONFLICTING_NAMES")
    df = df.drop_duplicates(["snapshot_date", "ticker"]).sort_values(
        ["snapshot_date", "ticker"]).reset_index(drop=True)
    return df


def download_membership(url: str = SOURCE_URL, timeout: int = 25,
                        max_bytes: int = 8_000_000) -> tuple[pd.DataFrame, dict]:
    if url != SOURCE_URL:
        raise ValueError("UNAPPROVED_MEMBERSHIP_ENDPOINT")
    request = Request(url, headers={"User-Agent": "BIST-PIT-research-audit/1.0"})
    with urlopen(request, timeout=timeout) as response:
        blob = response.read(max_bytes + 1)
    if len(blob) > max_bytes:
        raise ValueError("PIT_MEMBERSHIP_FILE_TOO_LARGE")
    result = read_membership(blob)
    return result, {"source": url, "sha256": sha256(blob).hexdigest(),
                    "bytes": len(blob), "snapshots": int(result.snapshot_date.nunique()),
                    "unique_tickers": int(result.ticker.nunique()),
                    "first_snapshot": result.snapshot_date.min().date().isoformat(),
                    "last_snapshot": result.snapshot_date.max().date().isoformat(),
                    "verified_official": False,
                    "publication_timestamp_verified": False}


@dataclass
class Membership:
    frame: pd.DataFrame

    def __post_init__(self):
        d = self.frame
        if d.empty or not REQUIRED.issubset(d.columns):
            raise ValueError("PIT_EMPTY_MEMBERSHIP")
        self.snapshots = sorted(pd.to_datetime(d["snapshot_date"]).unique())
        self.groups = {pd.Timestamp(k): set(g["ticker"]) for k, g in d.groupby("snapshot_date")}

    def at(self, date) -> set[str] | None:
        """Observed universe at preceding snapshot; None before first observation."""
        ts = pd.Timestamp(date).normalize()
        valid = [pd.Timestamp(k) for k in self.snapshots if pd.Timestamp(k) <= ts]
        if not valid:
            return None
        return set(self.groups[valid[-1]])

    def in_range(self, start, end) -> set[str]:
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        if end < start:
            raise ValueError("PIT_INVERTED_WINDOW")
        # Including membership at start preserves securities delisted mid-window.
        base = self.at(start) or set()
        for k in self.snapshots:
            dt = pd.Timestamp(k)
            if start < dt <= end:
                base.update(self.groups[dt])
        return base

    def audit(self, start, end, price_tickers: set[str], *, min_snapshot_days=120) -> dict:
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        if end < start:
            raise ValueError("PIT_INVERTED_WINDOW")
        dates = pd.date_range(start, end, freq="MS")
        records = []
        missing_price = set()
        late_months = []
        for d in dates:
            known = self.at(d)
            prev = [pd.Timestamp(k) for k in self.snapshots if pd.Timestamp(k) <= d]
            observation_age = (d - prev[-1]).days if prev else None
            stale = known is None or observation_age > min_snapshot_days
            if stale:
                late_months.append(d.strftime("%Y-%m"))
            members = known or set()
            absent = members - price_tickers
            missing_price.update(absent)
            records.append({"month": d.strftime("%Y-%m"),
                            "members": len(members), "priced_symbols": len(members & price_tickers),
                            "missing_price_symbols": len(absent),
                            "snapshot_age_days": observation_age,
                            "membership_stale": stale})
        all_tickers = self.in_range(start, end)
        unavailable = all_tickers - price_tickers
        # Missing delisted data and unknown liquidation proceeds prevent honest
        # closed-lot and portfolio NAV reconstruction; no fake zero exits.
        eligible = (not late_months and not unavailable and bool(records))
        return {
            "status": "FULL_HISTORY_RETEST_ELIGIBLE" if eligible else "NOT_VERIFIED",
            "period": [start.date().isoformat(), end.date().isoformat()],
            "snapshot_frequency": "QUARTERLY_OR_IRREGULAR_NOT_DAILY",
            "membership_asof_rule": "LATEST_SNAPSHOT_ON_OR_BEFORE_DECISION_DATE",
            "historical_tickers": len(all_tickers),
            "priced_historical_tickers": len(all_tickers & price_tickers),
            "missing_historical_price_tickers": len(unavailable),
            "missing_historical_price_examples": sorted(unavailable)[:40],
            "membership_stale_months": late_months[:50],
            "monthly_coverage": records,
            "exit_recovery_verified": False,
            "corporate_actions_verified": False,
            "point_in_time_fundamentals_verified": False,
            "survivorship_bias_resolved": False,
            "live_execution_verified": False,
        }


def save_json(path: str | Path, obj: dict):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
