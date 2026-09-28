# Adaptive BIST Orderflow Meta-Engine V2

End-of-day, free-data-only BIST equity selection engine running on GitHub Actions.
See **README_TR.md** for the full description and installation steps.

- Daily EOD run (18:25 TR): `python main.py`
- Monthly walk-forward research backtest (writes the learner's prior): `python backtest_optimizer.py`
- Weekly audit + synthetic self-test: `python longterm_auditor.py`, `python main.py --self-test`
- Dashboard: `streamlit run app.py`
