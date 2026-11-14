"""Isolated BIST historical-universe research; NEVER writes production data/.

Default: dated-membership and delisted-price availability audit.
--retest: optional explicitly unverified walk-forward sensitivity simulation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import traceback

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pit_universe import Membership, download_membership, read_membership, save_json
from pit_evidence import assess_evidence, write_missing_price_manifest


def check_prices(members: Membership, start: str, end: str, *, download: bool):
    universe = sorted(members.in_range(start, end))
    if not download:
        return {}, {"requested": len(universe), "loaded": 0, "mode": "MEMBERSHIP_ONLY",
                    "price_provider": "NOT_QUERIED"}
    import market_data as MD
    # No survivors-only prefilter: explicitly request symbols which later
    # disappear from BIST, and audit provider omissions.
    bars = MD.download_history(universe, start, end, min_rows=2)
    return bars, {"requested": len(universe), "loaded": len(bars),
                  "mode": "HISTORICAL_PRICE_QUERY", "price_provider": "YAHOO_ADJUSTED",
                  "requested_includes_no_longer_listed": True}



# Research-only loader: the committed fundamentals_hist.csv is a RAW
# financial-statement cache, not the derived PIT table expected by B.run().
RAW_FIELDS = {
    "ticker", "year", "period", "net_income", "equity", "revenue",
    "op_profit", "paid_in", "fin_debt",
}
PIT_FIELDS = {
    "ticker", "period_end", "avail_date", "net_income_ttm",
    "revenue_ttm", "op_profit_ttm", "rev_growth_pct", "equity",
    "fin_debt", "paid_in",
}


def load_research_fundamentals(path: Path) -> tuple[pd.DataFrame, dict]:
    """Build an isolated, as-of-compatible frame without writing production cache.

    Derived availability dates use the configured lag assumptions, NOT actual
    filing timestamps. This is suitable only for unverified research.
    """
    if not path.is_file():
        raise ValueError("PIT_FUNDAMENTALS_CACHE_MISSING")
    cache = pd.read_csv(path)
    if cache.empty:
        raise ValueError("PIT_FUNDAMENTALS_CACHE_EMPTY")
    columns = set(cache.columns)
    if PIT_FIELDS <= columns:
        result, mode = cache.copy(), "PRECOMPUTED_PIT_CACHE"
    elif RAW_FIELDS <= columns:
        import fundamentals_hist as FH
        for name in ("year", "period"):
            cache[name] = pd.to_numeric(cache[name], errors="raise")
            if cache[name].isna().any() or (cache[name] % 1 != 0).any():
                raise ValueError("PIT_FUNDAMENTALS_INVALID_" + name.upper())
            cache[name] = cache[name].astype(int)
        if not cache["period"].isin([3, 6, 9, 12]).all():
            raise ValueError("PIT_FUNDAMENTALS_INVALID_PERIOD")
        result = FH.build_point_in_time(cache)
        mode = "DERIVED_FROM_RAW_CACHE"
    else:
        missing = sorted(RAW_FIELDS - columns)
        raise ValueError("PIT_FUNDAMENTALS_SCHEMA_UNSUPPORTED: missing raw fields " +
                         ",".join(missing))
    if result.empty or not PIT_FIELDS <= set(result.columns):
        raise ValueError("PIT_FUNDAMENTALS_DERIVATION_INCOMPLETE")
    for date_name in ("period_end", "avail_date"):
        result[date_name] = pd.to_datetime(result[date_name], errors="coerce")
    if (result[["period_end", "avail_date"]].isna().any().any() or
            (result["avail_date"] <= result["period_end"]).any()):
        raise ValueError("PIT_FUNDAMENTALS_INVALID_AVAILABILITY_DATES")
    if result["ticker"].isna().any() or (result["ticker"].astype(str).str.strip() == "").any():
        raise ValueError("PIT_FUNDAMENTALS_INVALID_TICKER")
    metadata = {
        "status": "LOADED",
        "mode": mode,
        "records": int(len(result)),
        "tickers": int(result["ticker"].nunique()),
        "availability_rule": ("CONFIGURED_75_100_DAY_LAG_FROM_PERIOD_END"
                              if mode == "DERIVED_FROM_RAW_CACHE"
                              else "CACHED_DATES_UNVERIFIED"),
        "filing_publication_timestamps_verified": False,
        "restatement_history_verified": False,
    }
    return result, metadata


def run(args):
    if args.membership_csv:
        raw = Path(args.membership_csv).read_bytes()
        from hashlib import sha256
        frame = read_membership(raw)
        source = {"source": str(args.membership_csv), "sha256": sha256(raw).hexdigest(),
                  "verified_official": False, "publication_timestamp_verified": False}
    else:
        frame, source = download_membership()
    members = Membership(frame)
    bars, price = check_prices(members, args.start, args.end,
                               download=not args.membership_only)
    if args.membership_only:
        audit = {
            "status": "NOT_VERIFIED",
            "period": [args.start, args.end],
            "historical_tickers": len(members.in_range(args.start, args.end)),
            "price_coverage_status": "NOT_QUERIED",
            "symbols_with_any_price_series": None,
            "symbols_without_price_series": None,
            "months_below_95pct_price_coverage": [],
            "monthly_coverage": [],
            "survivorship_bias_resolved": False,
            "source_only": True,
        }
    else:
        audit = members.audit(args.start, args.end, bars)
    baseline = None
    p = ROOT / "data" / "backtest_report_v3.json"
    if p.exists():
        old = json.loads(p.read_text(encoding="utf-8"))
        baseline = {"generated_at": old.get("generated_at"),
                    "period": old.get("period"),
                    "universe_downloaded": old.get("universe_downloaded"),
                    "portfolio": old.get("portfolio"),
                    "closed_lots": old.get("closed_lots"),
                    "pit_universe_verified": False}
    retest = {"status": "NOT_RUN", "reason": "RESEARCH_RETEST_NOT_REQUESTED"}
    if args.retest:
        fundamentals_meta = {"status": "NOT_LOADED"}
        if not bars:
            retest = {"status": "NOT_TESTABLE", "reason": "HISTORICAL_PRICE_DATA_UNAVAILABLE"}
        else:
            try:
                import backtest_optimizer as B
                # Do not let this experiment call FH.load_history() and mutate
                # production cache: read existing best-effort PIT fundamentals.
                import config as C
                pf, fundamentals_meta = load_research_fundamentals(
                    Path(C.FUNDAMENTALS_CACHE_FILE))
                result = B.run(start=args.start, end=args.end, save=False,
                               data=bars, pit=pf, pit_membership=members)
                rep = result["report"]
                retest = {"status": "EXPLORATORY_INCOMPLETE_PIT",
                          "fundamentals": fundamentals_meta,
                          "portfolio": rep.get("portfolio"),
                          "closed_lots": rep.get("closed_lots"),
                          "period": rep.get("period"),
                          "universe_downloaded": rep.get("universe_downloaded"),
                          "pit_membership_filter_applied": rep.get("pit_membership_filter_applied"),
                          "not_verified_reasons": [
                              "SURVIVORSHIP_PRICE_COVERAGE_INCOMPLETE",
                              "DELISTING_TERMINAL_RECOVERY_UNKNOWN",
                              "FUNDAMENTAL_PUBLICATION_TIMES_UNVERIFIED",
                              "PIT_FINANCIAL_RESTATEMENTS_UNVERIFIED",
                              "UNADJUSTED_EXECUTION_SPREAD_SLIPPAGE_UNVERIFIED"]}
            except Exception as exc:
                retest = {"status": "NOT_TESTABLE", "reason": type(exc).__name__,
                          "detail": str(exc)[:300], "fundamentals": fundamentals_meta,
                          "traceback": traceback.format_exc(limit=12)[-5000:]}
    evidence = assess_evidence(audit, fundamentals_meta if args.retest else {},
                               Path(getattr(args, "evidence_dir", "research/evidence")))
    manifest = Path(args.output).with_name("missing_historical_prices.csv")
    if not args.membership_only:
        write_missing_price_manifest(manifest, audit)
    report = {
        "evidence_gates": evidence,
        "missing_price_manifest": str(manifest) if not args.membership_only else None,
        "schema": "BIST_PIT_RESEARCH_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": source, "prices": price, "audit": audit,
        "legacy_baseline": baseline, "exploratory_retest": retest,
        "conclusion": "NOT_VERIFIED",
        "validated_survivorship_free_alpha": False,
        "production_model_updated": False,
        "production_nav_replaced": False,
        "order_submission": False,
        "next_required_evidence": [
            "historical delisted OHLCV and corporate action adjustment",
            "ticker renaming/merger mapping and delisting proceeds",
            "original publication timestamps of filings",
            "independent and costs-stressed matched-window NAV"]
    }
    dest = Path(args.output)
    save_json(dest, report)
    print(json.dumps({"artifact": str(dest), "source_sha256": source["sha256"],
                      "historical_tickers": audit["historical_tickers"],
                      "priced": audit["symbols_with_any_price_series"],
                      "missing_historical_price_tickers": audit["symbols_without_price_series"],
                      "low_coverage_months": (None if args.membership_only else len(audit["months_below_95pct_price_coverage"])),
                      "retest": retest["status"], "conclusion": report["conclusion"]}, indent=2))
    # The artifact is written first; a non-testable requested retest then fails CI.
    # upload-artifact uses if: always() so diagnostics remain downloadable.
    if args.retest and retest["status"] == "NOT_TESTABLE":
        raise SystemExit(2)
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2017-01-01")
    ap.add_argument("--end", default="2026-10-01")
    ap.add_argument("--membership-csv", default=None)
    ap.add_argument("--membership-only", action="store_true")
    ap.add_argument("--evidence-dir", default="research/evidence")
    ap.add_argument("--retest", action="store_true")
    ap.add_argument("--output", default="artifacts/pit_universe_audit.json")
    a = ap.parse_args()
    run(a)
