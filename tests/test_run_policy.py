"""Regression coverage for delayed/early scheduled runs and refresh mode."""
import unittest

from run_policy import may_write_orders, may_send_routine_status


class RunPolicyTests(unittest.TestCase):
    def test_closed_session_eod_can_write_orders(self):
        self.assertTrue(may_write_orders(False))

    def test_closed_session_eod_can_notify(self):
        self.assertTrue(may_send_routine_status(False))

    def test_market_closed_refresh_never_writes_orders(self):
        self.assertFalse(may_write_orders(True))

    def test_market_closed_refresh_does_not_send_routine_telegram(self):
        self.assertFalse(may_send_routine_status(True))

    def test_truthy_refresh_is_safe(self):
        self.assertFalse(may_write_orders(1))
        self.assertFalse(may_send_routine_status(1))


if __name__ == "__main__":
    unittest.main()
