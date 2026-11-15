"""Safety policy for scheduled EOD versus background refresh execution.

A refresh may update CPI, regime and cash metadata, but it must not write
trade-order files or send the repetitive routine 'session not closed' alert.
Other critical alerts and monthly review messages remain enabled in main.py.
"""


def may_write_orders(refresh: bool) -> bool:
    return not bool(refresh)


def may_send_routine_status(refresh: bool) -> bool:
    return not bool(refresh)
