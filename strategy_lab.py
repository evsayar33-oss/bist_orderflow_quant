"""Strategy laboratory: search portfolio-construction rules on REAL data, choose them
walk-forward (no hindsight), and publish the winner for the live engine.

Why: the stock ranking itself is good (12m OOS IC ~0.09, t~2.6), but how many names,
which weights, how strict the entry gate and whether to rotate into gold / deposit
decide whether the portfolio beats the hurdle (CPI, USD+US inflation, gold, deposit).

Grid (V3.7, 192 variants):
  n_positions  : 3 / 5 / 8 / 12              (concentration — a SUM target needs conviction)
  buy/hold     : 95/75, 90/70, 85/60          (entry strictness + hysteresis)
  weighting    : inverse-vol / conviction (score-weighted, sqrt-vol aware)
  gate         : expected-return-vs-floor  or  rank-only (top scores fill the book)
  overlay      : none
                 dual   : dual momentum — stocks if BIST100 12m beats gold and deposit,
                          gold if gold is best, deposit if both lose to deposit
                 blend  : 50% gold when gold 12m > BIST100 12m (partial, not all-or-nothing);
                          50% deposit when both lose to deposit
                 core25 : a permanent 25% gold sleeve (TL-depreciation hedge), 75% stocks

Objective (the user's goal, measured on rolling 12-month windows):
  excess = portfolio 12m return - hurdle 12m return (max of CPI, USD+3%, gold, deposit)
  score  = 0.5 * 25th percentile(excess) + 0.5 * median(excess)   -> reliably beat the bar

Honesty: the reported performance is the META strategy — each year it uses the variant
that scored best ONLY on windows that had finished before that year (walk-forward model
selection, with hysteresis and a 1% switching cost). Picking the best variant on the full
history would be hindsight and is reported separately as "in-sample" for information only.
"""
from __future__ import annotations

import itertools
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import benchmarks as BM
import config as C
import inflation as INF
from meta_engine import GOLD_TICKER, exposure_from, plan_rebalance
from portfolio import apply_day, new_portfolio, weights as pf_weights

SWITCH_COST = 0.01
HYSTERESIS = 2.0          # score points a challenger must win by to replace the current variant
MIN_WINDOWS = 24


def variant_grid() -> List[Dict]:
    out = []
    short = {"inv_vol": "iv", "equal": "eq", "conviction": "cv"}
    for n, (b, h), w, g, o in itertools.product([3, 5, 8, 12], [(95.0, 75.0), (90.0, 70.0), (85.0, 60.0)],
                                                ["inv_vol", "conviction"], ["hurdle", "rank"],
                                                ["none", "dual", "blend", "core25"]):
        out.append({"name": f"n{n}_b{int(b)}_{short[w]}_{g}_{o}", "n_positions": n,
                    "buy_pct": b, "hold_pct": h, "weighting": w, "gate": g, "overlay": o})
    return out


def overlay_alloc(kind: str, day, bm: Optional[pd.DataFrame], crate: Optional[pd.Series]) -> (float, float):
    """(equity_fraction, gold_weight) decided with data up to `day` only."""
    if kind == "none" or bm is None or not len(bm):
        return 1.0, 0.0
    a = pd.Timestamp(day) - pd.Timedelta(days=365)
    w = BM.window_returns(bm, None, crate, a, day)
    eq, gold, dep = w.get("xu100"), w.get("gold"), w.get("deposit")
    if kind == "core25":
        return (0.75, 0.25) if np.isfinite(gold) else (1.0, 0.0)
    if not np.isfinite(eq) or not np.isfinite(dep):
        return 1.0, 0.0
    if kind == "trend":
        return (1.0 if eq > dep else 0.5), 0.0
    g = gold if np.isfinite(gold) else -np.inf
    if kind == "dual":
        if max(eq, g) < dep:
            return 0.0, 0.0
        return (1.0, 0.0) if eq >= g else (0.0, 1.0)
    if kind == "blend":
        if max(eq, g) < dep:
            return 0.5, 0.0
        return (0.5, 0.5) if g > eq else (1.0, 0.0)
    return 1.0, 0.0


def gold_bars(bm: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if bm is None or "gold_try" not in bm or bm["gold_try"].notna().sum() < 300:
        return None
    g = bm["gold_try"].dropna()
    return pd.DataFrame({"open": g, "close": g, "chg_pct": g.pct_change() * 100.0})


def simulate(variant: Dict, ctx: Dict) -> Dict:
    pf = new_portfolio(ctx["days"][0])
    nav_rows, lots_all = [], []
    gb = ctx.get("gold_bars")
    for day in ctx["days"]:
        bars = ctx["bars"][day]
        if gb is not None and day in gb.index and np.isfinite(gb.at[day, "chg_pct"]):
            bars = pd.concat([bars, gb.loc[[day]].rename(index={day: GOLD_TICKER})])
        ev, lots = apply_day(pf, bars, day, cash_yield_pct=ctx["cash_y"](day))
        lots_all.extend(lots)
        fr = ctx["frames"].get(day)
        if fr is not None:
            frame, st, reg = fr
            eq, gw = overlay_alloc(variant.get("overlay", "none"), day, ctx["bm"], ctx["crate"])
            if gb is None:
                gw = 0.0
            expo = exposure_from({"mode": "NORMAL"}, reg)
            orders, _ = plan_rebalance(pf, frame, st, expo, today_change=bars["chg_pct"], params=variant,
                                       equity_frac=eq, gold_w=gw)
            keep = [o for o in pf["pending"] if o.get("reason") == "CATASTROPHE_STOP"]
            pf["pending"] = keep + [o for o in orders if o["ticker"] not in {x["ticker"] for x in keep}]
        nav_rows.append({"tarih": day, "nav": pf["nav"], "exposure": sum(v for k, v in pf_weights(pf).items() if k != GOLD_TICKER),
                         "xu100": ctx["xu"](day)})
    return {"nav": pd.DataFrame(nav_rows), "lots": lots_all, "open": {t: round((p["level"] - 1) * 100, 2)
                                                                      for t, p in pf["positions"].items()}}


def hurdle_table(month_ends: pd.DatetimeIndex, bm, cpi, crate) -> pd.Series:
    out = {}
    for i in range(12, len(month_ends)):
        a, b = month_ends[i - 12], month_ends[i]
        out[b] = BM.window_returns(bm, cpi, crate, a, b)["hurdle"]
    return pd.Series(out, dtype=float)


def excess_series(nav: pd.DataFrame, hurdles: pd.Series) -> pd.Series:
    m = nav.set_index("tarih")["nav"].resample("ME").last().dropna()
    r12 = (m / m.shift(12) - 1) * 100
    h = hurdles.reindex(r12.index)
    return (r12 - h).dropna()


def score(ex: pd.Series) -> float:
    if len(ex) < 12:
        return -np.inf
    return float(0.5 * ex.quantile(0.25) + 0.5 * ex.median())


def run_lab(ctx: Dict, cpi, bench: Dict, default: Dict, variants: Optional[List[Dict]] = None, log=print) -> Dict:
    variants = variants or variant_grid()
    if not any(v["name"] == default["name"] for v in variants):
        variants = [default] + variants
    results = {}
    for i, v in enumerate(variants):
        results[v["name"]] = simulate(v, ctx)
        if (i + 1) % 12 == 0:
            log(f"   strateji laboratuvarı: {i + 1}/{len(variants)} varyant")
    any_nav = next(iter(results.values()))["nav"]
    mends = any_nav.set_index("tarih")["nav"].resample("ME").last().dropna().index
    H = hurdle_table(mends, bench.get("bm"), cpi, bench.get("crate"))
    ex = {k: excess_series(r["nav"], H) for k, r in results.items()}

    # ---- walk-forward meta selection (yearly, hysteresis, switching cost)
    daily = {k: r["nav"].set_index("tarih")["nav"] for k, r in results.items()}
    days = daily[default["name"]].index
    years = sorted(set(days.year))
    current, choices = default["name"], {}
    meta_ret = pd.Series(0.0, index=days)
    for y in years:
        y0 = pd.Timestamp(year=y, month=1, day=1)
        hist = {k: e[e.index < y0] for k, e in ex.items()}
        if len(hist[default["name"]]) >= MIN_WINDOWS:
            sc = {k: score(e) for k, e in hist.items()}
            best = max(sc, key=sc.get)
            if best != current and sc[best] > sc.get(current, -np.inf) + HYSTERESIS:
                current = best
        choices[int(y)] = current
        mask = days.year == y
        r = daily[current].pct_change().fillna(0.0)
        meta_ret[mask] = r[mask]
        if y != years[0] and choices.get(y - 1) and choices[y - 1] != current:
            first = np.argmax(mask)
            meta_ret.iloc[first] -= SWITCH_COST
    meta_nav = (1 + meta_ret).cumprod()
    xu = results[default["name"]]["nav"].set_index("tarih")["xu100"]
    meta_df = pd.DataFrame({"tarih": days, "nav": meta_nav.to_numpy(), "xu100": xu.reindex(days).to_numpy()})

    # ---- final (live) choice = the same rule applied today on all finished windows
    sc_all = {k: score(e) for k, e in ex.items()}
    final = choices[years[-1]]
    best_now = max(sc_all, key=sc_all.get)
    if best_now != final and sc_all[best_now] > sc_all.get(final, -np.inf) + HYSTERESIS:
        final = best_now
    table = []
    for v in variants:
        e = ex[v["name"]]
        n = results[v["name"]]["nav"]["nav"]
        yrs = max((days[-1] - days[0]).days / 365.25, 1e-9)
        table.append({"name": v["name"], "score": round(sc_all[v["name"]], 2) if np.isfinite(sc_all[v["name"]]) else None,
                      "beat_hurdle_pct": round(float((e > 0).mean() * 100), 1) if len(e) else None,
                      "median_excess_pp": round(float(e.median()), 1) if len(e) else None,
                      "cagr_pct": round(float((n.iloc[-1] / n.iloc[0]) ** (1 / yrs) - 1) * 100, 2),
                      "max_dd_pct": round(float((n / n.cummax() - 1).min() * 100), 2)})
    table.sort(key=lambda r: -(r["score"] if r["score"] is not None else -1e9))
    vmap = {v["name"]: v for v in variants}
    meta_ex = excess_series(meta_df, H)
    # Adoption guard: the lab's pick goes live only if the walk-forward SELECTION PROCEDURE
    # itself beat the default rules out-of-sample (otherwise selection noise > signal).
    active_years = [y for y, c in choices.items()]
    first_sel = next((y for y in years if len(ex[default["name"]][ex[default["name"]].index < pd.Timestamp(year=y, month=1, day=1)]) >= MIN_WINDOWS), None)
    if first_sel is not None:
        cut = pd.Timestamp(year=first_sel, month=12, day=31)
        m_sc = score(meta_ex[meta_ex.index >= cut])
        d_sc = score(ex[default["name"]][ex[default["name"]].index >= cut])
    else:
        m_sc = d_sc = -np.inf
    adopt = bool(np.isfinite(m_sc) and m_sc >= d_sc)
    live_pick = vmap[final] if adopt else vmap[default["name"]]
    return {"variants_tested": len(variants), "table": table, "choices_by_year": choices,
            "selected": live_pick, "selected_score": sc_all[live_pick["name"]],
            "lab_best": vmap[final], "lab_best_score": sc_all[final],
            "adopted": adopt, "meta_oos_score": None if not np.isfinite(m_sc) else round(m_sc, 2),
            "default_oos_score": None if not np.isfinite(d_sc) else round(d_sc, 2),
            "meta_nav": meta_df, "meta_beat_hurdle_pct": round(float((meta_ex > 0).mean() * 100), 1) if len(meta_ex) else None,
            "meta_median_excess_pp": round(float(meta_ex.median()), 1) if len(meta_ex) else None,
            "results": results, "default_name": default["name"], "hurdles": H}
