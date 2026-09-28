# BIST Real-Return Engine V3

Long-horizon, monthly-rebalanced BIST portfolio whose objective is to beat Turkish CPI over 12 months (secondary: XU100). Free data only, runs on GitHub Actions. See **README_TR.md**.

- Daily run after close: `python main.py`
- Walk-forward research backtest: `python backtest_optimizer.py`
- Weekly audit / self-test: `python longterm_auditor.py`, `python main.py --self-test`
- Dashboard: `streamlit run app.py`
