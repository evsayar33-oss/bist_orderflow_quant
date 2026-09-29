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

ENGINE_VERSION = "3.4.0"
STRATEGY_NAME = "ADAPTIVE_BIST_REAL_RETURN_ENGINE_V3"

# ---------------------------------------------------------------- objective (edit here)
HORIZON_MONTHS = 12                 # holding / label horizon
BENCHMARK_PRIMARY = "CPI"           # win = real (CPI-deflated) return > 0
BENCHMARK_SECONDARY = "XU100"       # reported: excess return vs index
# V3.4 multi-benchmark hurdle: a pick must be expected to beat ALL of these (12m).
HURDLE_COMPONENTS = ["cpi", "usd", "gold", "deposit"]
US_INFLATION_PCT = 3.0              # "dolar enflasyonu": USD must keep its real value
MIN_EDGE_OVER_HURDLE_PCT = 3.0      # expected 12m return must exceed the hurdle by this much

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
MAX_MONTHLY_SNAPSHOTS = 180         # 15 years of monthly cross-sections

# ---------------------------------------------------------------- universe
SCAN_LIMIT = 450
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
HARD_STOP_FROM_ENTRY_PCT = 50.0    # unconditional sell at the next open
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
