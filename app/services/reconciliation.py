from typing import Tuple

TOLERANCE = 0.01


def reconcile(opening_balance: float, closing_balance: float, transactions: list) -> Tuple[bool, float]:
    total_credit = sum(t["amount"] for t in transactions if t["direction"] == "credit")
    total_debit = sum(t["amount"] for t in transactions if t["direction"] == "debit")
    computed_closing = opening_balance + total_credit - total_debit
    diff = round(computed_closing - closing_balance, 2)
    return abs(diff) <= TOLERANCE, diff
