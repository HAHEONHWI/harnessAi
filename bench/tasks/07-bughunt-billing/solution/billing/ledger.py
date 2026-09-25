"""Tracks invoices and payments."""
import datetime as dt


class Ledger:
    def __init__(self):
        self._invoices = {}  # number -> {"total", "due", "payments": [(date, cents)]}

    def record_invoice(self, number, total_cents, due):
        if number in self._invoices:
            raise ValueError(f"duplicate invoice {number}")
        self._invoices[number] = {"total": total_cents, "due": due, "payments": []}

    def record_payment(self, number, cents, on):
        if cents <= 0:
            raise ValueError("payments must be positive")
        self._invoices[number]["payments"].append((on, cents))

    def balance(self, number):
        inv = self._invoices[number]
        return inv["total"] - sum(c for _, c in inv["payments"])

    def paid_on(self, number):
        """Date the invoice became fully paid, or None."""
        inv = self._invoices[number]
        paid = 0
        for on, cents in sorted(inv["payments"], key=lambda p: p[0]):
            paid += cents
            if paid >= inv["total"]:
                return on
        return None

    def is_late(self, number, as_of, grace_days=3):
        """Late when full payment arrived after due + grace days, or is still missing after that day."""
        deadline = self._invoices[number]["due"] + dt.timedelta(days=grace_days)
        paid = self.paid_on(number)
        return (paid > deadline) if paid else (as_of > deadline)

    def open_invoices(self, as_of):
        """Numbers with a positive balance, oldest due date first (then by number)."""
        rows = [(inv["due"], n) for n, inv in self._invoices.items() if self.balance(n) > 0]
        return [n for _, n in sorted(rows)]
