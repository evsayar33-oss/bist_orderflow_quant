"""Point-in-time historical fundamentals for the backtest (free, best effort).

Source: Is Yatirim public financial-statement endpoint (same one used by the
open-source `isyatirimhisse` package). Income-statement items are year-to-date
cumulative in Turkish filings, so trailing-twelve-month values are rebuilt as
    TTM(y,q) = YTD(y,q) + FY(y-1) - YTD(y-1,q).
A statement is only visible to the backtest after period end + a publication
lag (75 days quarterly, 100 days annual) -> no look-ahead.
If the endpoint is unavailable the backtest continues with price factors only
and reports the coverage; live fundamentals come from TradingView.
"""
from __future__ import annotations

import concurrent.futures
import os
import time
import unicodedata
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import requests

import config as C

URL = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/Common/Data.aspx/MaliTablo"
GROUPS = ["XI_29", "UFRS"]

PATTERNS = {
    "net_income": {"exact": ["donem net kari veya zarari", "net donem kari veya zarari", "net donem kari/zarari",
                             "donem kari (zarari)", "net profit after taxes", "net profit", "net income"],
                   "contains": ["net donem kar", "donem net kar", "net profit"]},
    "equity": {"exact": ["ozkaynaklar", "toplam ozkaynaklar", "ana ortakliga ait ozkaynaklar",
                         "shareholders equity", "total equity"],
               "contains": ["ozkaynaklar", "equity"]},
    "revenue": {"exact": ["satis gelirleri", "hasilat", "net satislar", "net sales", "revenue", "sales"],
                "contains": ["satis gelir", "hasilat", "net sales"]},
    "op_profit": {"exact": ["faaliyet kari veya zarari", "esas faaliyet kari veya zarari", "faaliyet kari",
                            "operating profit", "operating income"],
                  "contains": ["faaliyet kar", "operating profit"]},
    "paid_in": {"exact": ["odenmis sermaye", "paid-in capital", "paid in capital", "sermaye"],
                "contains": ["odenmis sermaye", "paid-in capital"]},
}
DEBT_EXACT = ["finansal borclar", "financial liabilities", "financial loans", "borrowings"]
FLOW_ITEMS = ["net_income", "revenue", "op_profit"]


def _norm(s) -> str:
    s = str(s or "").strip().lower()
    s = s.replace("ı", "i").replace("İ", "i")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.replace(" ", " ").split())


def _num(v):
    try:
        x = float(str(v).replace(",", "")) if isinstance(v, str) else float(v)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def _extract(rows: List[Dict], k: int) -> Dict[str, float]:
    """Concept values for period slot k (1..4) from one MaliTablo response."""
    descs = [(_norm(r.get("itemDescTr")), _norm(r.get("itemDescEng")), r) for r in rows]
    out = {}
    for concept, pat in PATTERNS.items():
        val = np.nan
        for mode in ("exact", "contains"):
            for p in pat[mode]:
                for tr, en, r in descs:
                    hit = (tr == p or en == p) if mode == "exact" else (p in tr or p in en)
                    if hit:
                        v = _num(r.get(f"value{k}"))
                        if np.isfinite(v):
                            val = v
                            break
                if np.isfinite(val):
                    break
            if np.isfinite(val):
                break
        out[concept] = val
    debt = [_num(r.get(f"value{k}")) for tr, en, r in descs if tr in DEBT_EXACT or en in DEBT_EXACT]
    debt = [d for d in debt if np.isfinite(d)]
    out["fin_debt"] = float(np.sum(debt)) if debt else np.nan
    return out


def fetch_ticker(ticker: str, years: List[int]) -> pd.DataFrame:
    recs = []
    group = None
    for y in sorted(years, reverse=True):
        params = {"companyCode": ticker, "exchange": "TRY"}
        for i, p in enumerate([12, 9, 6, 3], start=1):
            params[f"year{i}"], params[f"period{i}"] = y, p
        rows = []
        for g in ([group] if group else GROUPS):
            try:
                r = requests.get(URL, params={**params, "financialGroup": g},
                                 headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
                r.raise_for_status()
                rows = r.json().get("value", []) or []
            except Exception:
                rows = []
            if rows and any(np.isfinite(_num(x.get("value1"))) or np.isfinite(_num(x.get("value2"))) for x in rows):
                group = g
                break
        if not rows:
            continue
        for i, p in enumerate([12, 9, 6, 3], start=1):
            v = _extract(rows, i)
            if any(np.isfinite(x) for x in v.values()):
                recs.append({"ticker": ticker, "year": y, "period": p, **v})
        time.sleep(0.1)
    return pd.DataFrame(recs)


def build_point_in_time(raw: pd.DataFrame) -> pd.DataFrame:
    """Adds TTM flows, growth, period_end and availability date."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    out = []
    for t, g in raw.groupby("ticker"):
        g = g.drop_duplicates(subset=["year", "period"], keep="last").set_index(["year", "period"]).sort_index()
        rows = []
        for (y, p), r in g.iterrows():
            rec = {"ticker": t, "year": y, "period": p}
            for item in FLOW_ITEMS:
                cur = r[item]
                if p == 12:
                    ttm = cur
                else:
                    fy = g[item].get((y - 1, 12), np.nan)
                    prev = g[item].get((y - 1, p), np.nan)
                    ttm = cur + fy - prev
                rec[f"{item}_ttm"] = ttm
            for item in ("equity", "fin_debt", "paid_in"):
                rec[item] = r[item]
            rows.append(rec)
        d = pd.DataFrame(rows).set_index(["year", "period"])
        prev_rev = d["revenue_ttm"].copy()
        prev_rev.index = pd.MultiIndex.from_tuples([(y + 1, p) for y, p in prev_rev.index])
        d["rev_growth_pct"] = (d["revenue_ttm"] / prev_rev.reindex(d.index) - 1.0) * 100.0
        d = d.reset_index()
        d["period_end"] = pd.to_datetime(d["year"].astype(str) + "-" + d["period"].astype(str) + "-01") + pd.offsets.MonthEnd(0)
        lag = np.where(d["period"] == 12, C.FUND_LAG_ANNUAL_DAYS, C.FUND_LAG_QUARTER_DAYS)
        d["avail_date"] = d["period_end"] + pd.to_timedelta(lag, unit="D")
        out.append(d)
    res = pd.concat(out, ignore_index=True)
    return res.replace([np.inf, -np.inf], np.nan)


def load_history(tickers: List[str], start_year: int, refresh_years: int = 2, max_workers: int = 6) -> pd.DataFrame:
    """Cached, incremental download. Returns point-in-time table (may be empty)."""
    cache = pd.DataFrame()
    if os.path.exists(C.FUNDAMENTALS_CACHE_FILE):
        try:
            cache = pd.read_csv(C.FUNDAMENTALS_CACHE_FILE)
        except Exception:
            cache = pd.DataFrame()
    this_year = pd.Timestamp.now().year
    jobs = {}
    for t in tickers:
        have = cache[cache["ticker"] == t] if not cache.empty else pd.DataFrame()
        if have.empty:
            years = list(range(start_year - 1, this_year + 1))
        else:
            years = list(range(this_year - refresh_years, this_year + 1))
        jobs[t] = years
    fetched = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_ticker, t, ys): t for t, ys in jobs.items()}
        for f in concurrent.futures.as_completed(futs):
            try:
                d = f.result()
                if not d.empty:
                    fetched.append(d)
            except Exception:
                continue
    raw = pd.concat([cache] + fetched, ignore_index=True) if fetched or not cache.empty else pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()
    raw = raw.drop_duplicates(subset=["ticker", "year", "period"], keep="last")
    try:
        os.makedirs(C.DATA_DIR, exist_ok=True)
        raw.to_csv(C.FUNDAMENTALS_CACHE_FILE, index=False)
    except Exception:
        pass
    return build_point_in_time(raw)


def point_in_time(pit: pd.DataFrame, date, tickers: List[str]) -> pd.DataFrame:
    """Latest statement per ticker that was public on `date`."""
    if pit is None or pit.empty:
        return pd.DataFrame(columns=["ticker"])
    d = pit[(pit["avail_date"] <= pd.Timestamp(date)) & pit["ticker"].isin(tickers)]
    if d.empty:
        return pd.DataFrame(columns=["ticker"])
    return d.sort_values("period_end").groupby("ticker").tail(1)
