# BIST 2017–2026: Point-in-Time Universe / Survivorship Research V1

## Aim and baseline

Production `backtest_optimizer.backtest_universe()` uses **today's TradingView list** (+ a static large-cap list). The 2026-11-02 existing research report states 2017–2026, 145 closed lots, 90.3% winning lots, 72.38% nominal CAGR and -40.31% maximum drawdown. Those historical outcomes are **not proven** under the point-in-time listed universe and missing delisting recovery.

This PR adds a **separate, nonproduction** PIT-membership *sensitivity test*, not a claim that the 90.3% was recomputed or corrected already. The existing `backtest_optimization.yml`, learned weights, live trading, Telegram output, active stops, strategy revision and committed state files are unchanged.

## Historical-source audit

- **Official research source category:** Borsa İstanbul lists historical share data, code changes, and companies whose trading lines have permanently closed. Historical datasets may require DataStore licensing/access: https://www.borsaistanbul.com/veriler/pay-piyasasi-verileri .
- **Free secondary lead (not official independently verified):** `GocmenCo/bist-constituents`, `data/bist_constituents.csv`, https://github.com/GocmenCo/bist-constituents . The maintainer describes 134 review snapshots, 927 historical ticker codes, 2000–2026-10-01, including companies missing today. These are **maintainer claims**, not audited exchange-level totals. The PR CI downloads the CSV, checks schema, and pins the observed SHA-256 `fbda6e52154b3ec3d2ffa0583db4921a2ffaf737e8617b9dacfdc67f2c3680aa`; any upstream content change now fails closed until explicitly reviewed. It writes source URL and digest to the artifact. It does not commit third-party data to this repository.
- **Caveats:** effective quarterly/irregular snapshot dates are not proof of original publication time; a stock listing/delisting between snapshots may be invisible. Index tier tags are not the membership universe. Symbol changes/mergers and delisting proceeds cannot be resolved from ticker codes alone. The free source contains no OHLCV or filing publication timestamps.

## Mechanics

`research/pit_universe.py` parses the membership snapshots and prevents lookahead via latest-snapshot-on/before-decision-date filtering. It includes companies appearing or disappearing during the test period in the historical-ticker *union*. It audits **as-of daily OHLCV history**, requiring 260 past sessions and a last bar within 7 calendar days; merely getting a Yahoo ticker response does not count as coverage. A monthly report names deficient windows and missing-price tickers. Membership older than 120 days is marked stale. Invalid ticker/date/conflicting names fail.

`backtest_optimizer.run(..., pit_membership=membership, save=False)` has an **opt-in** research mode that filters eligible stocks at each historical rebalance and uses only already available share-capital data. Default production calls still follow the existing code path. `research/run_pit_audit.py --retest` activates the opt-in exploratory full evaluation, but its output is labelled `EXPLORATORY_INCOMPLETE_PIT`, even if it runs successfully.

**Not yet verified:** disappearing firms' historical adjusted prices, terminal recoveries/mergers, original financial report publication times, true independent holdout and cost/fill realizability. Those are mandatory for claiming survivorship-free alpha. We deliberately do **not** force missing delisted securities to zero or magically borrow present-day tickers.

## How to run

After merge: GitHub Actions → **BIST PIT Universe Research — historical survivorship audit** → Run workflow, choose:

- `membership_only`: authenticates the *shape and provenance* of the downloaded historical catalog, no price claims.
- `price_coverage` (default): queries Yahoo for all historical codes from 2017 through 2026-10-01 (including ones delisted since), audits usability by month and missing series, and uploads `bist-pit-universe-audit` JSON.
- `exploratory_retest`: same fetch plus opt-in PIT universe screen and as-of paid-in-capital filter, re-simulates if possible; output intentionally remains **unverified**, not a replacement for production backtest.

The workflow writes **only an Actions artifact**, does not commit generated reports and checks that committed production baseline files were not changed. External providers may rate-limit or lack delisted prices, potentially making the 2017–2026 retest impossible. Such a result is a valid diagnosis, **not proof that survivorship bias is fixed**.

Regressions: `python -m unittest discover -s tests -p test_pit_universe.py -v`. The source smoke checks the actual third-party CSV on pull requests; the online price download is manual to avoid accidental rate limit/expense.

## Alpha acceptance criteria

Do not promote a strategy or claim the baseline 90.3% is representative unless historic universe/identifier lineage, delisting recovery, true as-of disclosures, missing-price/fill data and matched-cost out-of-sample evidence are addressed. In line with `quant_superlab`, a blocked gate is **NOT_VERIFIED**, not a positive performance claim.
