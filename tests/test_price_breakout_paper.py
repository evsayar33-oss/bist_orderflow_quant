import copy
import unittest
import pandas as pd
from research.price_breakout_paper import new_state, step, picks, score, last_completed_weekday, asof_monthly_snapshot


class PaperLedgerTests(unittest.TestCase):
    def test_latest_snapshot_selection_has_no_future_data_leak(self):
        frame = pd.DataFrame({
            "tarih": pd.to_datetime(["2026-10-30", "2026-11-18",
                                    "2026-11-20", "2026-11-23"]),
            "ticker": ["OLD", "AAA", "BBB", "FUTURE"],
        })
        date, rows = asof_monthly_snapshot(frame, "2026-11-20")
        self.assertEqual(date.strftime("%Y-%m-%d"), "2026-11-20")
        self.assertEqual(rows["ticker"].tolist(), ["BBB"])
        date, rows = asof_monthly_snapshot(frame, "2026-11-19")
        self.assertEqual(date.strftime("%Y-%m-%d"), "2026-11-18")
        self.assertEqual(rows["ticker"].tolist(), ["AAA"])
        date, rows = asof_monthly_snapshot(frame, "2026-11-01")
        self.assertIsNone(date)
        self.assertIsNone(rows)

    def test_calendar_cutoff_survives_delayed_cron(self):
        scenarios = [
            ("2026-11-20 18:15", "2026-11-20"),  # completed Friday
            ("2026-11-20 18:14", "2026-11-19"),  # Friday not closed
            ("2026-11-21 02:18", "2026-11-20"),  # delayed cron on Saturday
            ("2026-11-22 22:00", "2026-11-20"),  # Sunday
            ("2026-11-23 09:00", "2026-11-20"),  # Monday not closed
            ("2026-11-23 18:20", "2026-11-23"),
        ]
        for stamp, date in scenarios:
            with self.subTest(stamp=stamp):
                self.assertEqual(
                    last_completed_weekday(pd.Timestamp(stamp, tz="Europe/Istanbul")), date)

    def test_delayed_runner_starts_at_last_completed_day_not_retroactive_trade(self):
        cutoff = last_completed_weekday(pd.Timestamp("2026-11-21 02:18", tz="Europe/Istanbul"))
        state = new_state(cutoff)
        self.assertEqual(state["start"], "2026-11-20")
        self.assertEqual(state["trades"], [])
        self.assertIsNone(state["last_day"])

    def test_initial_books_are_isolated(self):
        s = new_state("2026-11-16")
        self.assertEqual(len(s["books"]), 4)
        self.assertTrue(s["no_real_orders"])
        self.assertEqual(len(s["trades"]), 0)

    def test_next_open_execution_and_costs(self):
        s = new_state("2026-11-16")
        names = ["AAA", "BBB", "CCC", "DDD", "EEE"]
        prices = {t: {"open": 100, "close": 110} for t in names}
        signal = {"baseline": names, "price_breakout": names}
        s = step(s, "2026-11-16", prices, signal)
        self.assertEqual(len(s["trades"]), 0)
        self.assertIsNotNone(s["books"]["price_breakout_1pct"]["pending"])
        s = step(s, "2026-11-17", prices)
        self.assertEqual(len(s["trades"]), 20)
        self.assertGreater(s["books"]["price_breakout_1pct"]["nav"],
                           s["books"]["price_breakout_2pct"]["nav"])
        self.assertIsNone(s["books"]["price_breakout_1pct"]["pending"])

    def test_duplicate_day_rejected_without_mutation(self):
        s = new_state("2026-11-16")
        s = step(s, "2026-11-16", {"AAA": {"open": 10, "close": 10}})
        old = copy.deepcopy(s)
        with self.assertRaisesRegex(ValueError, "NON_MONOTONIC"):
            step(s, "2026-11-16", {"AAA": {"open": 10, "close": 10}})
        self.assertEqual(s, old)

    def test_missing_price_fails_closed_without_mutation(self):
        s = new_state("2026-11-16")
        s = step(s, "2026-11-16", {"AAA": {"open": 10, "close": 10}},
                 {"baseline": ["AAA"], "price_breakout": ["AAA"]})
        old = copy.deepcopy(s)
        with self.assertRaisesRegex(ValueError, "MISSING_EXECUTION_BAR"):
            step(s, "2026-11-17", {"BBB": {"open": 10, "close": 10}})
        self.assertEqual(s, old)

    def test_picks_requires_all_factor_fields(self):
        with self.assertRaisesRegex(ValueError, "MISSING_MONTHLY_SNAPSHOT_FIELDS"):
            picks(pd.DataFrame({"ticker": ["AAA"]}), "price_breakout")

    def test_paper_does_not_promote_model(self):
        s = new_state("2026-11-16")
        self.assertNotIn("production", s)
        self.assertNotIn("orders", s)


if __name__ == "__main__":
    unittest.main()
