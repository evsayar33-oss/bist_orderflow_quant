from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = Path.cwd() if (Path.cwd() / "main.py").exists() else HERE

src_guard = HERE / "autonomy_guard.py"
dst_guard = REPO / "autonomy_guard.py"
if src_guard.resolve() != dst_guard.resolve():
    if dst_guard.exists():
        backup_guard = dst_guard.with_suffix(dst_guard.suffix + ".pre_kurun_backup")
        if not backup_guard.exists():
            shutil.copy2(dst_guard, backup_guard)
    shutil.copy2(src_guard, dst_guard)


def patch(path: Path, old: str, new: str, label: str):
    if not path.exists():
        raise SystemExit(f"MISSING: {path}")
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"ANCHOR ERROR {label}: expected 1 match, found {count}")
    backup = path.with_suffix(path.suffix + ".pre_kurun_backup")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text.replace(old, new), encoding="utf-8")


main = REPO / "main.py"
patch(
    main,
    "from meta_engine import score_market\n",
    "from meta_engine import score_market\nfrom autonomy_guard import evaluate_autonomy_guard\n",
    "main import",
)
patch(
    main,
    "    scored = score_market(valid, state)\n    signal_log = log_signals(scored, state)\n",
    '''    scored = score_market(valid, state)\n\n    # Regime Stress-Test / Drift / Safe-Mode: observe the fully scored market\n    # first, then apply the guard without changing the underlying model scores.\n    signal_log_for_guard = load_signal_log()\n    guard_result = evaluate_autonomy_guard(\n        state,\n        features=scored,\n        regime=state.get("market_regime"),\n        regime_confidence=float(state.get("regime_confidence", 0.0)) / 100.0,\n        performance_returns=(signal_log_for_guard["ret_t3"] if "ret_t3" in signal_log_for_guard.columns else None),\n        data_quality_score=float(quality.get("score", 0.0)),\n        row_count=len(valid),\n        min_rows=30,\n        project="orderflow",\n    )\n    base_threshold = float(state.get("risk_guards", {}).get("min_signal_score", 72.0))\n    effective_threshold = base_threshold + float(guard_result.get("signal_threshold_add", 0.0))\n    if guard_result.get("block_new_entries"):\n        scored["eligible"] = False\n    else:\n        scored["eligible"] = scored["eligible"].astype(bool) & (\n            pd.to_numeric(scored["meta_score"], errors="coerce") >= effective_threshold\n        )\n\n    signal_log = log_signals(scored, state)\n''',
    "main guard",
)
patch(
    main,
    '        f"🧭 Rejim: <b>{regime}</b> | Güven: <b>%{confidence:.1f}</b>",\n',
    '        f"🧭 Rejim: <b>{regime}</b> | Güven: <b>%{confidence:.1f}</b>",\n        f"🛡️ Otonomi: <b>{state.get(\'autonomy_guard\', {}).get(\'mode\', \'NORMAL\')}</b> | Maruziyet x{state.get(\'autonomy_guard\', {}).get(\'exposure_multiplier\', 1.0):.2f}",\n',
    "report guard line",
)

# Evening audit: performance drift is evaluated even if no fresh score frame is present.
aud = REPO / "longterm_auditor.py"
patch(
    aud,
    "        state = apply_learning(signal_log, state)\n    else:\n",
    '''        state = apply_learning(signal_log, state)\n        # Performance drift is evaluated independently from the morning score frame.\n        evaluate_autonomy_guard(\n            state,\n            features=None,\n            regime=state.get("market_regime"),\n            regime_confidence=float(state.get("regime_confidence", 0.0)) / 100.0,\n            performance_returns=(signal_log["ret_t3"] if "ret_t3" in signal_log.columns else None),\n            data_quality_score=float(state.get("data_quality", {}).get("score", 100.0)),\n            row_count=state.get("data_quality", {}).get("rows"),\n            min_rows=30,\n            project="orderflow",\n        )\n    else:\n''',
    "audit guard call",
)
# Add import for the audit file.
patch(
    aud,
    "from state_manager import load_ai_state, load_lifecycle_signals, load_signal_log, save_ai_state, save_lifecycle_signals\n",
    "from state_manager import load_ai_state, load_lifecycle_signals, load_signal_log, save_ai_state, save_lifecycle_signals\nfrom autonomy_guard import evaluate_autonomy_guard\n",
    "audit import",
)
# Also make the audit CLI report mention the current mode.
patch(
    aud,
    '    lines = ["🧪 <b>ADAPTIVE BIST META-ENGINE AUDIT</b>"]\n',
    '    lines = ["🧪 <b>ADAPTIVE BIST META-ENGINE AUDIT</b>"]\n',
    "audit report anchor check",
)

# Compile changed modules before declaring success.
for name in ("autonomy_guard.py", "main.py", "longterm_auditor.py"):
    subprocess.check_call([sys.executable, "-m", "py_compile", str(REPO / name)])
print("OK: BIST Orderflow Kur-Unut guard installed. Backups: *.pre_kurun_backup")
