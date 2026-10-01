"""Central configuration — Adaptive BIST Real-Return Engine V3 (long-horizon "al-unut").

Objective: a monthly-rebalanced portfolio of BIST stocks whose holdings are
expected to BEAT TURKISH CPI (TÜFE) over 12 months, with excess return over
XU100 as the secondary yardstick.
Only free data: TradingView public scanner, Yahoo Finance (yfinance),
Is Yatirim public financial-statement endpoint, TCMB EVDS (free key, optional)
and FRED (keyless) for CPI.
"""
from __future__ import annotations

import os

ENGINE_VERSION = "3.13.0"
STRATEGY_NAME = "ADAPTIVE_BIST_REAL_RETURN_ENGINE_V3"

# ---------------------------------------------------------------- objective (edit here)
HORIZON_MONTHS = 12                 # holding / label horizon
BENCHMARK_PRIMARY = "CPI"           # win = real (CPI-deflated) return > 0
BENCHMARK_SECONDARY = "XU100"       # reported: excess return vs index
# V3.4 multi-benchmark hurdle: a pick must be expected to beat ALL of these (12m).
HURDLE_COMPONENTS = ["cpi", "usd", "gold", "deposit"]
US_INFLATION_PCT = 3.0              # "dolar enflasyonu": USD must keep its real value
MIN_EDGE_OVER_HURDLE_PCT = 3.0      # margin (pp) added on top of the target
# V3.6: TARGET = SUM of the components (CPI + USD + gold + deposit) + margin  -> user's goal.
# "max" = old rule (strongest single alternative). The strongest single alternative is still
# used as the minimum ENTRY FLOOR: a stock that cannot even beat the best alternative is never bought.
HURDLE_MODE = os.environ.get("BOQ_HURDLE_MODE", "max")   # V3.13: "max" = beat CPI and the BEST single alternative (sum is unreachable by design)

# ---------------------------------------------------------------- paths
DATA_DIR = os.environ.get("BOQ_DATA_DIR", "data")
MONTHLY_SNAPSHOT_FILE = os.path.join(DATA_DIR, "monthly_snapshots.csv")
TRADE_LOG_FILE = os.path.join(DATA_DIR, "trade_log.csv")
NAV_FILE = os.path.join(DATA_DIR, "nav.csv")
STATE_FILE = os.path.join(DATA_DIR, "engine_state_v3.json")
RESEARCH_PRIOR_FILE = os.path.join(DATA_DIR, "research_prior_v3.json")
BACKTEST_REPORT_FILE = os.path.join(DATA_DIR, "backtest_report_v3.json")
CPI_CACHE_FILE = os.path.join(DATA_DIR, "cpi_tr.csv")
CPI_MANUAL_FILE = os.path.join(DATA_DIR, "cpi_manual.csv")   # optional user upload: tarih,cpi
FUNDAMENTALS_CACHE_FILE = os.path.join(DATA_DIR, "fundamentals_hist.csv")
STRATEGY_CONFIG_FILE = os.path.join(DATA_DIR, "strategy_config.json")   # written by the strategy lab
MAX_MONTHLY_SNAPSHOTS = 180         # 15 years of monthly cross-sections

# ---------------------------------------------------------------- universe
SCAN_LIMIT = 1000                  # V3.7: no practical cap (BIST ~600 stocks)
BACKTEST_UNIVERSE_MAX = 800        # V3.7: backtest on all BIST stocks (TradingView list), not 103 large caps
MIN_CROSS_SECTION = 30
MIN_MEDIAN_VALUE_TRADED_TL = 20_000_000.0   # 3-month median daily value traded
MIN_HISTORY_SESSIONS = 260                  # price factors need ~1 year of bars
LIMIT_MOVE_PCT = 9.5
CORP_ACTION_MOVE_PCT = 10.5

# ---------------------------------------------------------------- factors
PRICE_FACTORS = [
    "mom_12_1",        # 12-month return skipping the last month
    "high_52w",        # close / 52-week high
    "trend_consistency",  # share of positive months in last 12
    "low_vol",         # minus 6-month daily volatility
    "low_beta",        # minus 1-year beta to XU100
    "dd_resilience",   # minus 1-year max drawdown
    "liquidity",       # log 3-month median value traded
]
FUNDAMENTAL_FACTORS = [
    "roe",             # return on equity (TTM)
    "earnings_yield",  # net income / market cap
    "book_yield",      # equity / market cap
    "sales_yield",     # revenue / market cap
    "op_margin",       # operating margin
    "low_leverage",    # minus financial debt / equity
    "div_yield",       # dividend yield (live only)
    "real_growth",     # revenue growth minus CPI inflation
]
FACTORS = PRICE_FACTORS + FUNDAMENTAL_FACTORS

# Prior 12-month rank ICs (literature: momentum, low-risk, quality, value in EM).
# Replaced by the walk-forward research prior, then refined by live evidence.
PRIOR_IC = {
    "mom_12_1": 0.030, "high_52w": 0.025, "trend_consistency": 0.020, "low_vol": 0.030,
    "low_beta": 0.010, "dd_resilience": 0.015, "liquidity": 0.000,
    "roe": 0.030, "earnings_yield": 0.030, "book_yield": 0.020, "sales_yield": 0.010,
    "op_margin": 0.015, "low_leverage": 0.015, "div_yield": 0.010, "real_growth": 0.015,
}
PRIOR_STRENGTH_HAND = 12.0        # effective independent 12m periods behind the hand prior
PRIOR_STRENGTH_RESEARCH = 40.0
OMEGA_SHRINK = 0.25
MAX_ABS_WEIGHT = 0.30
MAX_WEIGHT_L1_STEP = 0.30

# ---------------------------------------------------------------- learning (units = monthly cross-sections)
LABEL_HORIZON = HORIZON_MONTHS     # overlap of monthly labels (Newey-West lags = horizon-1)
MIN_LEARN_DATES = 24
CHALLENGER_MIN_TEST_DATES = 12
PROMOTION_MIN_IC_GAIN = 0.01
PROMOTION_MIN_T = 1.0
ROLLBACK_WINDOW = 18
ROLLBACK_IC = -0.02
ROLLBACK_T = -1.5
REGIME_MIN_DATES = 36

# ---------------------------------------------------------------- portfolio rules
TARGET_POSITIONS = 12
BUY_PCT = 85.0                     # composite percentile to enter
HOLD_PCT = 60.0                    # hysteresis: keep while above this (low turnover)
DEFAULT_PCT_CUTOFF = BUY_PCT
PCT_CUTOFF_CANDIDATES = [75.0, 80.0, 85.0, 90.0]
MIN_EXPECTED_REAL_PCT = 3.0        # expected 12m real return (after costs) required to buy
MAX_POSITION_W = 0.15
MAX_PER_SECTOR = 3
REBALANCE_BAND = 0.05              # only trade existing holdings if weight drift > 5pp
# Drawdown handling (V3.2, evidence from the real-CPI backtest: an unconditional -35% stop
# caused 25 of 46 exits and locked in losses in a ~40%-vol market):
CATASTROPHE_FROM_PEAK_PCT = 35.0   # drawdown FLAG: 35% below highest close since entry ...
CATASTROPHE_FROM_ENTRY_PCT = 30.0  # ... or 30% below entry -> sold at the monthly review ONLY if the
                                   #     thesis also failed (score below the BUY cut-off)
HARD_STOP_FROM_ENTRY_PCT = 1000.0  # V3.8: no forced stop (every name is re-decided within 6 months; flag stays informational)
# Exposure: a beat-CPI investor is fully invested; the guard/regime only trim, never halve it
EXPOSURE_BY_MODE = {"NORMAL": 1.0, "WATCH": 0.9, "RECOVERY": 0.75, "SAFE": 0.0}
REGIME_EXPOSURE_FLOOR = 0.8
COST_ROUND_TRIP_PCT = 0.50
COST_ONE_WAY = COST_ROUND_TRIP_PCT / 200.0
CASH_YIELD_ANNUAL_PCT = 0.0        # fallback when the TL money-market rate cannot be loaded
# Idle cash is assumed to sit in a TL money-market fund: TCMB weighted average funding
# cost (EVDS TP.APIFON4) minus a haircut, after withholding tax.
CASH_RATE_SERIES = os.environ.get("EVDS_CASH_SERIES", "TP.APIFON4")
CASH_HAIRCUT_PP = 2.0
CASH_TAX = 0.15
POLICY_RATE_CACHE_FILE = os.path.join(os.environ.get("BOQ_DATA_DIR", "data"), "cash_rate_tr.csv")

# ---------------------------------------------------------------- regime model
REGIME_TICKER_INDEX = "XU100.IS"
REGIME_TICKER_FX = "TRY=X"
REGIME_HISTORY_START = "2012-01-01"
REGIME_REFIT_DAYS = 30
REGIME_STATES = 3

# ---------------------------------------------------------------- fundamentals (history)
FUND_LAG_QUARTER_DAYS = 75         # point-in-time availability after period end
FUND_LAG_ANNUAL_DAYS = 100

MARKET_TZ = "Europe/Istanbul"


# ---------------------------------------------------------------- V3.8 stock-only tranche engine
# Research on the real 581-stock panel (2017-2026, walk-forward): holding rules ("keep while score
# >= p60") won in 2022-26 but lost badly in 2017-21; buying the fresh top names each month and
# holding every monthly cohort for a fixed period was robust in BOTH halves. The portfolio is
# 100% stocks: no gold, no cash sleeve, no strategy switching.
TRANCHE_N = 5                     # new names bought each month (top of the ranking)
TRANCHE_MONTHS = 6                # each monthly cohort is held 6 months -> ~10 names on average
TRANCHE_SECTOR_CAP = 2            # max names from one sector inside a monthly cohort
MAX_NAME_W = 0.15                 # portfolio-level single-name cap (a name re-picked in several cohorts)
PORTFOLIO_SECTOR_CAP = None       # tested 0.35 on real data: no benefit -> off (per-cohort cap of 2 stays)
CONFIDENCE_FILE_KEY = "confidence_model"

# ---- V3.8.1 drawdown controls (entry filters + weighting), tested on the real panel
TRANCHE_WEIGHTING = "equal"        # "equal" | "inv_vol" (within each monthly cohort)
ENTRY_MAX_VOL_PCTILE = None        # e.g. 0.8 -> skip the most volatile 20% of stocks at entry
ENTRY_MIN_MCAP_PCTILE = None       # e.g. 0.3 -> skip the smallest 30% (by market value) at entry
DEFENSIVE_IN_DOWNTREND = False     # index < 200d avg -> new picks only from large (top 50% mcap) & lower-beta half

# ---- V3.9 conditional ATR stop (+ breakeven), tested on the real panel
STOP_MODE = None                  # None | "all" | "low_conf" | "high_risk"
STOP_ATR_MULT = 3.0               # stop distance = mult x ATR(~1.25 x daily sigma) below entry
STOP_LOW_CONF = 0.58              # "low_conf": confidence below this gets a stop
STOP_HIGH_RISK_PCTILE = 0.7       # "high_risk": vol or lottery rank above this gets a stop
BREAKEVEN_TRIGGER_PCT = 15.0      # once +15% above entry, stop moves to the entry price

# ---- V3.9 health / live-vs-test
BACKTEST_NAV_FILE = os.path.join(DATA_DIR, "backtest_nav_v3.csv")

# ---- V3.9.1 profit rules (tested; adopted only if they help in BOTH 2017-21 and 2022-26)
TRIM_WINNERS = True               # False -> an overweight winner may drift up to +10pp before being trimmed
WINNER_EXTENSION = False          # True -> at expiry keep names up >= +50% AND still in the top 20% of scores

# ---- V3.10 value-trap guard: cheap stocks that are STILL falling underperform (research: every period)
VALUE_TRAP_MODE = "filter"        # ON (V3.10): skip cheap stocks still in the bottom third of 'turn' (3m return + distance from 52w low)
# real-data test: return ~unchanged across thresholds (63-67%/yr), beat-BIST100 80% -> 85-91%, sum target 17% -> 18-23%
VALUE_TRAP_CUT = 1 / 3            # "filter": skip names whose 'turn' percentile is below this

# ---- V3.12: don't realise a loss at cohort expiry ---------------------------------------------------
# A name whose 6-month cohort expires while it is BELOW its entry price is kept for another cohort if its
# score is still in the top 30% (composite percentile >= 70), up to 18 extra months.
# Real-data walk-forward (same engine, only this rule changed):
#   BIST 2017-26: lots closed with a gain 68% -> 91%, avg lot +56% -> +79%, CAGR 67.4% -> 66.9%, max DD -41.4% -> -40.4%
#   US   2015-26: lots closed with a gain 51% -> 67%, avg lot +2.3% -> +8.2%, CAGR 9.1% -> 10.6%, max DD -57.6% -> -47.0%
#   Neighbouring settings (score >= 60/70/80, 6/12/18 months) moved the same way.
LOSER_EXTENSION = {"min_score_pct": 70, "max_extra_months": 18}

# ---- V3.13: two extra signals in the SELECTION ranking (learned 15-factor composite stays the base) -------------
# Tested one by one in the full walk-forward engine (12 candidates), then in combinations (8 runs):
#   sector momentum (sector average 12-1 momentum) and "cheap AND profitable" (earnings-yield rank x ROE rank)
#   were the only two that improved BOTH halves alone; together (0.5 / 0.5):
#   2017-26 CAGR 66.9% -> 81.1% · halves 55.6/79.3 -> 66.1/97.7 · 1 TL 2017-25: 129 -> 259 · max DD -40.4% -> -44.2%
#   Selecting only on the first half would have picked the same combination (it was also best there).
#   Weight 1.0 on sector momentum hurt the first half -> moderate weights kept.
EXTRA_BLEND = {"sector_mom": 0.5, "ep_x_quality": 0.5}
