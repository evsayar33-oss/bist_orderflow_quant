"""One-off diagnostic export: the exact price panel + benchmarks the backtest uses.
Output goes to the separate `diag-data` branch (main and the dashboard are untouched)."""
import json
import os

import pandas as pd

import backtest_optimizer as B
import benchmarks as BM
import market_data as MD
import regime_model as RM

OUT = "diag_out"
os.makedirs(OUT, exist_ok=True)
uni = B.backtest_universe()
data = MD.download_history(uni, "2012-01-01", None, min_rows=300)
print(f"indirilen hisse: {len(data)} / {len(uni)}")
for fld in ("open", "close", "volume"):          # exactly what factors.wide_from_history uses
    wide = pd.DataFrame({t: g[fld] for t, g in data.items()}).sort_index()
    wide.to_csv(f"{OUT}/px_{fld}.csv.gz", compression="gzip", float_format="%.6g")
RM.download_regime_series(start="2011-01-01").to_csv(f"{OUT}/regime.csv")
BM.download_benchmarks(start="2011-01-01").to_csv(f"{OUT}/bench.csv")
json.dump({"universe": uni, "downloaded": sorted(data)}, open(f"{OUT}/universe.json", "w"))
print("✅ tamam")
