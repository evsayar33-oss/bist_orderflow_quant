# PIT BIST — five-blocker evidence remediation (research only)

This change **does not resolve the five blockers by itself**. It creates a reproducible, fail-closed inventory so that incomplete evidence cannot be presented as validated alpha. The current research sample has 726 historical tickers; the previous run observed 610 price series and 116 missing. These counts must be recalculated on every new run.

## 1. Historical OHLCV / delisting coverage
The `bist-pit-universe-audit` artifact now contains `missing_historical_prices.csv` with **all** unavailable ticker codes, not just the first 40. The JSON includes `symbols_without_price_series_all` and the actual count of months below 95% coverage (the previous displayed month list was truncated to 50). For each ticker, obtain a dated, source-attributed OHLCV history from a licensed/official historical source, reconcile symbol aliases and corporate actions, and rerun the same coverage check. A symbol alias alone is not proof of a historical return.

## 2. Delistings / corporate actions
Optional `research/evidence/ticker_events.csv` must have:
`ticker,event_type,effective_date,successor_ticker,cash_recovery_per_share,source_url,observed_at`.
Record terminal cash recovery, liquidation, merger consideration, split/rights and delisting dates with evidence. Missing fields and unsupported zero-recovery assumptions are not acceptable. The validator checks **schema only**, not whether those records are independently authentic. No synthetic delisting fills are applied.

## 3. Original financial publication timestamps
Optional `research/evidence/filing_events.csv`:
`ticker,period_end,first_published_at,source_url,observed_at,version_id`.
Source the original KAP filings, not a current revised fundamentals table. An as-of decision can use a statement only after its **original** public timestamp. The existing 75/100-day lag is a conservative heuristic, not a verified disclosure timestamp. This evidence is not automatically injected into trading signals.

## 4. Restatements and revision vintages
Preserve the exact original version of each filing and its later revisions; map `version_id` to immutable original documents and their publication times. Historical factors must be reconstructed from the vintage available at each decision date, not today's revised value. Merely adding a `filing_events.csv` row does not reconstruct the financial history.

## 5. Execution and cost realism
Optional `research/evidence/execution_samples.csv`:
`ticker,trade_date,side,reference_price,executed_price,commission_pct,source_url`.
Collect timestamp-matched quotes/fills, BIST transaction taxes/fees as applicable, lot size, volume participation, auction limits, bid-ask spread and slippage; verify against executed samples. Rerun the *same* OOS window with baseline, moderate and severe cost assumptions and reconcile trade-by-trade fills. Do not treat a constant round-trip cost as independently verified.

## Acceptance conditions
1. Every missing historical security is resolved with source-proven OHLCV or documented as an explicit untestable exclusion; coverage must be remeasured.
2. Delisting proceeds and all corporate actions are reconciled to original records.
3. Filing availability timestamps and original restatement vintages are rebuilt and causality-tested.
4. The same 2022–2026 window is used for legacy-vs-PIT comparisons; both simulations must have identical benchmark, costs and universe eligibility assumptions.
5. Independent execution samples and sensitivity scenarios are validated; no `survivorship_bias_resolved=true` or `validated_survivorship_free_alpha=true` before all five gates pass.

**Safety:** research-only; no production NAV, model, strategy, orders, or backtest baseline overwritten. An evidence CSV passing basic schema validation never turns a gate green automatically.
