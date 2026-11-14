"""Fail-closed, research-only evidence gates for the five unresolved PIT risks.

Evidence must be supplied from independently auditable sources. This module
does not invent missing price histories, filing timestamps or execution fills.
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd

REQUIRED = {
    "ticker_events": {"ticker", "event_type", "effective_date", "successor_ticker",
                      "cash_recovery_per_share", "source_url", "observed_at"},
    "filing_events": {"ticker", "period_end", "first_published_at", "source_url",
                      "observed_at", "version_id"},
    "execution_samples": {"ticker", "trade_date", "side", "reference_price",
                          "executed_price", "commission_pct", "source_url"},
}


def _evidence(path: Path, fields: set[str], date_fields: tuple[str, ...]) -> tuple[pd.DataFrame, dict]:
    if not path.is_file():
        return pd.DataFrame(), {"status": "MISSING", "records": 0}
    try:
        d = pd.read_csv(path, dtype=str)
        if d.empty:
            return pd.DataFrame(), {"status": "EMPTY", "records": 0}
        missing = sorted(fields - set(d.columns))
        if missing:
            return pd.DataFrame(), {"status": "INVALID_SCHEMA", "missing_columns": missing}
        if d[list(fields)].isna().any().any():
            return pd.DataFrame(), {"status": "INCOMPLETE_ROWS", "records": len(d)}
        for name in date_fields:
            d[name] = pd.to_datetime(d[name], utc=True, errors="coerce")
            if d[name].isna().any():
                return pd.DataFrame(), {"status": "INVALID_DATE", "column": name}
        if d["source_url"].str.strip().eq("").any():
            return pd.DataFrame(), {"status": "MISSING_PROVENANCE"}
        return d, {"status": "SCHEMA_VALID_ONLY", "records": len(d),
                   "source_independently_verified": False}
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        return pd.DataFrame(), {"status": "READ_ERROR", "detail": str(exc)[:200]}


def assess_evidence(audit: dict, fundamentals: dict, root: Path) -> dict:
    """Evidence inventory, not a performance certification."""
    missing = sorted(set(audit.get("symbols_without_price_series_all") or
                         audit.get("symbols_without_price_series_examples") or []))
    ticker, tmeta = _evidence(root / "ticker_events.csv", REQUIRED["ticker_events"],
                              ("effective_date", "observed_at"))
    filing, fmeta = _evidence(root / "filing_events.csv", REQUIRED["filing_events"],
                              ("period_end", "first_published_at", "observed_at"))
    fills, emeta = _evidence(root / "execution_samples.csv", REQUIRED["execution_samples"],
                             ("trade_date",))
    if not filing.empty and (filing["first_published_at"] <= filing["period_end"]).any():
        fmeta["status"] = "INVALID_CAUSALITY"
    if not ticker.empty:
        relevant = ticker[ticker["ticker"].isin(missing)]
        tmeta["missing_price_symbols_with_event_record"] = int(relevant.ticker.nunique())
        tmeta["unresolved_missing_price_symbols"] = sorted(set(missing) - set(relevant.ticker))
    if not fills.empty:
        for col in ("reference_price", "executed_price", "commission_pct"):
            vals = pd.to_numeric(fills[col], errors="coerce")
            if vals.isna().any() or (vals <= 0).any() and col != "commission_pct":
                emeta["status"] = "INVALID_EXECUTION_VALUES"
                break
            if col == "commission_pct" and (vals < 0).any():
                emeta["status"] = "INVALID_EXECUTION_VALUES"
                break
    blockers = {
        "historical_price_coverage": {
            "status": "BLOCKED" if audit.get("symbols_without_price_series") or
                     audit.get("months_below_95pct_price_coverage_count", 0) else "NOT_CERTIFIED",
            "missing_price_series": audit.get("symbols_without_price_series"),
            "missing_symbols": missing,
            "months_below_95pct": audit.get("months_below_95pct_price_coverage_count"),
        },
        "delisting_and_corporate_actions": {"status": "BLOCKED", "evidence": tmeta,
                                            "reason": "INDEPENDENT_TERMINAL_SETTLEMENT_NOT_VERIFIED"},
        "original_filing_publication": {"status": "BLOCKED", "evidence": fmeta,
                                        "reason": "ORIGINAL_PUBLICATION_TIMESTAMPS_NOT_VERIFIED"},
        "historical_financial_revisions": {
            "status": "BLOCKED", "evidence": fmeta,
            "reason": "ORIGINAL_FILING_VERSIONS_NOT_RECONSTRUCTED",
            "fundamentals_mode": fundamentals.get("mode")},
        "spread_slippage_execution": {
            "status": "BLOCKED", "evidence": emeta,
            "reason": "INDEPENDENT_FILLS_AND_MATCHED_WINDOW_STRESS_NOT_VERIFIED"},
    }
    return {"status": "EVIDENCE_INCOMPLETE", "all_five_resolved": False,
            "evidence_dir": str(root), "gates": blockers,
            "notes": ["Schema validation is not independent source verification",
                      "Ticker event rows do not restore missing OHLCV",
                      "No inference of survivorship-free performance from incomplete data"]}


def write_missing_price_manifest(path: Path, audit: dict) -> None:
    """Produce an actionable queue for verified historical data acquisition."""
    symbols = sorted(set(audit.get("symbols_without_price_series_all") or
                         audit.get("symbols_without_price_series_examples") or []))
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ticker": symbols, "status": "PRICE_SERIES_MISSING",
                  "source_url": "", "resolution": "",
                  "verified_by": ""}).to_csv(path, index=False)
