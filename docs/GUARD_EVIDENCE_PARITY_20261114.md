# 2026-11-14 — BIST risk-guard parity and evidence integrity

## Fixed now

1. **Actual order gate:** `autonomy_guard.mode == SAFE` or `block_new_entries == true` stops a new monthly cohort and all new BUY orders. Existing holdings are kept; outstanding protective SELL orders remain handled by `main.monthly_review`. The gate is visible in `health.checks`. No new cohort is recorded while SAFE.
2. **Zero-exposure bug:** numeric zero no longer becomes 1.0 via `or 1.0`. Explicit zero exposure queues SELL for held names, records `RISK_EXPOSURE_ZERO` and clears cohorts so a subsequent risk recovery cannot buy previously blocked ghost cohorts. Invalid/non-finite exposure halts new planning. Any risk-calculation exception blocks new buys instead of silently authorizing new 100% exposure.
3. **Honest forward evidence:** live IC 0/6 months, calibration 0/30 resolved recommendations, and live closed-lot hit rates below 30 observations are **BEKLEME** / not validated, not green or 0%-vs-90% proof. Weekly Telegram and dashboard retain counts. Comparison requires valid live BIST100 endpoints, never backward-fills a missing benchmark, states sample size and the fact historical windows overlap.

## Prior research from quant_superlab: method, not transferable alpha

`quant_superlab/AGENTS.md` and `lab/validation.py` explicitly require out-of-sample evidence, cost stress, effective sample adequacy and a preregistered acceptance gate. These methodological protections are applied to BIST reporting; **crypto alpha, stop distances or winning parameters were not transplanted**.

## Known limitations NOT resolved by this PR

- The 2017–2026 BIST backtest uses today's liquid listed securities. **Survivorship bias remains** until a historical point-in-time universe with delistings, symbol changes, and as-of fundamentals is assembled and tested. The %90.3 backtest closed-lot hit rate must not be treated as a validated prospective hit rate.
- In the current snapshot, live has **1 closed lot** (`NOT_SELECTED`, held about a day), versus **145** backtest lots (all `COHORT_EXPIRY`, average 12.3 months). These are not like-for-like exits. The new reporting suppresses ungrounded win-rate comparisons; it cannot invent missing comparable live observations.
- `HARD_STOP_FROM_ENTRY_PCT=1000` and `RISK_MODE=None` are **existing explicit choices**, tested historically with some protective overlays degrading USD-adjusted performance. This PR does not secretly enable an unvalidated stop or claim protection against future drawdowns. The inactive protection policy is now visible in health status.
- A descriptive 8.9th historical percentile is a risk warning, **not a statistically independent p-value**. Overlapping windows and historical survivorship must be resolved before claiming expected live profitability.

## Acceptance checks

The PR CI must pass `python -m unittest discover -s tests -p 'test_guard_evidence.py' -v` and the existing `python main.py --self-test`. After merge, run the self-test workflow, then examine the **next post-close** `health.guard_policy`, pending orders, and weekly Telegram output before external execution. No stored historical NAV, trade log, research prior, or learned strategy weights are rewritten in this PR.

The portfolio can still lose money. Passing these tests establishes internal guard correctness and more honest evidence labels, **not positive alpha**.
