"""Cold-start helper (V2).

V1 generated bootstrap signals with a different formula than the live model.
In V2 the cold start is handled properly by the walk-forward research backtest,
which runs the LIVE scoring code on adjusted historical data and writes
data/research_prior.json (the learner's Bayesian prior + OOS calibration).

    python bootstrap_history.py      # identical to: python backtest_optimizer.py
"""
from backtest_optimizer import main

if __name__ == "__main__":
    main()
