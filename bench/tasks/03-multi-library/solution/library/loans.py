import datetime as dt

from .catalog import Catalog
from .models import Member

LOAN_DAYS = 14
FINE_PER_DAY = 25
FINE_CAP = 1000
BLOCK_AT = 500
HOLD_DAYS = 3


class LoanError(Exception):
    pass


class LoanService:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self.members = {}
        self.active = {}  # book_id -> (member_id, due date)
        # Every checkout/return: {"kind", "date", "book_id", "member_id", "fine_cents"}
        self.events = []
        self.queues = {}  # book_id -> [member_id, ...]
        self.holds = {}  # book_id -> last day of the current holder's hold

    def add_member(self, member: Member) -> None:
        self.members[member.id] = member

    def _expire_holds(self, book_id: str, on: dt.date) -> None:
        queue = self.queues.get(book_id, [])
        end = self.holds.get(book_id)
        while end is not None and on > end and queue:
            queue.pop(0)
            end = end + dt.timedelta(days=HOLD_DAYS) if queue else None
        if end is None or not queue:
            self.holds.pop(book_id, None)
        else:
            self.holds[book_id] = end

    def checkout(self, book_id: str, member_id: str, on: dt.date) -> dt.date:
        self.catalog.get(book_id)
        if member_id not in self.members:
            raise LoanError(f"unknown member {member_id}")
        if book_id in self.active:
            raise LoanError(f"{book_id} is already checked out")
        if self.members[member_id].fines_cents >= BLOCK_AT:
            raise LoanError(f"{member_id} owes fines")
        self._expire_holds(book_id, on)
        queue = self.queues.get(book_id, [])
        if book_id in self.holds:
            if queue[0] != member_id:
                raise LoanError(f"{book_id} is on hold for {queue[0]}")
            queue.pop(0)
            del self.holds[book_id]
        elif member_id in queue:
            queue.remove(member_id)
        due = on + dt.timedelta(days=LOAN_DAYS)
        self.active[book_id] = (member_id, due)
        self.events.append({"kind": "checkout", "date": on, "book_id": book_id, "member_id": member_id, "fine_cents": 0})
        return due

    def return_book(self, book_id: str, on: dt.date) -> int:
        if book_id not in self.active:
            raise LoanError(f"{book_id} is not checked out")
        member_id, due = self.active.pop(book_id)
        late_days = max(0, (on - due).days)
        fine = min(FINE_CAP, late_days * FINE_PER_DAY)
        self.members[member_id].fines_cents += fine
        self.events.append({"kind": "return", "date": on, "book_id": book_id, "member_id": member_id, "fine_cents": fine})
        if self.queues.get(book_id):
            self.holds[book_id] = on + dt.timedelta(days=HOLD_DAYS)
        return fine

    def pay_fine(self, member_id: str, cents: int) -> None:
        member = self.members[member_id]
        if cents <= 0 or cents > member.fines_cents:
            raise ValueError("invalid payment")
        member.fines_cents -= cents

    def reserve(self, book_id: str, member_id: str, on: dt.date) -> None:
        self.catalog.get(book_id)
        if member_id not in self.members:
            raise LoanError(f"unknown member {member_id}")
        if book_id not in self.active:
            raise LoanError(f"{book_id} is not checked out")
        if self.active[book_id][0] == member_id:
            raise LoanError(f"{member_id} already has {book_id}")
        queue = self.queues.setdefault(book_id, [])
        if member_id in queue:
            raise LoanError(f"{member_id} already reserved {book_id}")
        queue.append(member_id)

    def reservations(self, book_id: str):
        return list(self.queues.get(book_id, []))

    def due_date(self, book_id: str) -> dt.date:
        return self.active[book_id][1]
