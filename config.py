"""Central configuration for Adaptive BIST Orderflow Meta-Engine V2.

Every tunable constant lives here so that the live engine, the learner and the
walk-forward backtest share exactly the same definitions.
Only free data sources are used: TradingView public scanner, Yahoo Finance
(yfinance) and an optional, best-effort Is Yatirim takas endpoint.
"""
from __future__ import annotations

import os

ENGINE_VERSION = "2.0.0"
STRATEGY_NAME = "ADAPTIVE_BIST_ORDERFLOW_META_ENGINE_V2"

# ---------------------------------------------------------------- paths
# BOQ_DATA_DIR lets the self-test run in a throw-away folder so synthetic data
# can never contaminate production files again.
DATA_DIR = os.environ.get("BOQ_DATA_DIR", "data")
SNAPSHOT_FILE = os.path.join(DATA_DIR, "snapshots_eod.csv")
LEDGER_FILE = os.path.join(DATA_DIR, "signals_ledger.csv")
STATE_FILE = os.path.join(DATA_DIR, "engine_state.json")
RESEARCH_PRIOR_FILE = os.path.join(DATA_DIR, "research_prior.json")
BACKTEST_REPORT_FILE = os.path.join(DATA_DIR, "backtest_report.json")

# Keep ~1 trading year of live snapshots in the repo (file-size control).
# Long-horizon evidence comes from the backtest research prior.
MAX_SNAPSHOT_SESSIONS = 260

# ---------------------------------------------------------------- universe
MIN_VALUE_TRADED_TL = 10_000_000.0     # hard liquidity floor for eligibility
SCAN_LIMIT = 450                        # TradingView rows (sorted by value traded)
MIN_CROSS_SECTION = 30                  # min names for a valid cross-section
LIMIT_MOVE_PCT = 9.5                    # BIST daily price limit is +-10%
CORP_ACTION_MOVE_PCT = 10.5             # raw move beyond limit => corporate action / bad print

# ---------------------------------------------------------------- factors
# Each factor is defined so that "higher = expected better" under the prior.
# The learner may shrink or even flip the sign when evidence demands it.
FACTORS = [
    "mom_3m",       # 3-month relative momentum
    "rs_1m",        # 1-month relative strength
    "range_pos",    # position inside the 1-month high/low range
    "rev_1d",       # 1-day reversal (minus today's change)
    "flow_clv",     # close-location-value x relative volume (daily money-flow proxy)
    "vol_surge",    # log relative volume
    "low_vol",      # minus ATR% (low-volatility anomaly)
    "liquidity",    # log value traded
    "takas_delta",  # day-over-day change in top-broker custody concentration (optional)
]

# Prior factor ICs (rank IC units). Used only until real evidence exists:
# research_prior.json (walk-forward backtest) replaces them, live data refines.
PRIOR_IC = {
    "mom_3m": 0.020,
    "rs_1m": 0.015,
    "range_pos": 0.020,
    "rev_1d": 0.010,
    "flow_clv": 0.010,
    "vol_surge": 0.000,
    "low_vol": 0.010,
    "liquidity": 0.005,
    "takas_delta": 0.000,
}
PRIOR_STRENGTH_HAND = 15.0       # effective independent dates behind the hand prior
PRIOR_STRENGTH_RESEARCH = 60.0   # effective independent dates behind the backtest prior
OMEGA_SHRINK = 0.25              # shrink factor-correlation matrix toward identity
MAX_ABS_WEIGHT = 0.35
MAX_WEIGHT_L1_STEP = 0.30        # max L1 change of weights per promotion

# ---------------------------------------------------------------- labels / trade plan
LABEL_HORIZON = 5                # sessions for the IC label (entry next open -> close t+5)
MAX_HOLD = 10                    # time barrier in sessions
STOP_ATR_MULT = 2.0
STOP_MIN_PCT = 4.0
STOP_MAX_PCT = 12.0
TARGET_RR = 1.5                  # target distance = RR x stop distance
BREAKEVEN_TRIGGER_R = 1.0        # after +1R, stop moves to entry + BREAKEVEN_BUFFER
BREAKEVEN_BUFFER_PCT = 0.2
COST_ROUND_TRIP_PCT = 0.50       # commission + BSMV + slippage, both sides

# ---------------------------------------------------------------- selection
DEFAULT_PCT_CUTOFF = 90.0        # composite percentile cut-off before calibration
PCT_CUTOFF_CANDIDATES = [80.0, 85.0, 90.0, 95.0]
MIN_EXPECTED_EDGE_PCT = 0.25     # required expected net return (%) per trade
MAX_CANDIDATES = 8              # new entries per day (before exposure scaling)
MAX_OPEN_POSITIONS = 12         # portfolio-level cap on simultaneous positions
RISK_PER_TRADE_PCT = 1.0         # capital risked to the stop per trade
MAX_POSITION_PCT = 15.0

# ---------------------------------------------------------------- learning
MIN_LEARN_DATES = 20             # resolved cross-sections before live evidence counts
CHALLENGER_MIN_TEST_DATES = 20
PROMOTION_MIN_IC_GAIN = 0.005
PROMOTION_MIN_T = 1.0
ROLLBACK_WINDOW = 30
ROLLBACK_IC = -0.02
ROLLBACK_T = -1.5
REGIME_MIN_DATES = 60

# ---------------------------------------------------------------- regime model
REGIME_TICKER_INDEX = "XU100.IS"
REGIME_TICKER_FX = "TRY=X"
REGIME_HISTORY_START = "2018-01-01"
REGIME_REFIT_DAYS = 7
REGIME_STATES = 3

# ---------------------------------------------------------------- scheduling
MARKET_TZ = "Europe/Istanbul"
