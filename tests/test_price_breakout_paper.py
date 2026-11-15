import copy
import unittest
import pandas as pd
from research.price_breakout_paper import new_state, step, picks, score


class PaperLedgerTests(unittest.TestCase):
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
