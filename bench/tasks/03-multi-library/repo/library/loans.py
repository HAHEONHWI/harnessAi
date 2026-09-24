import datetime as dt

from .catalog import Catalog
from .models import Member

LOAN_DAYS = 14


class LoanError(Exception):
    pass


class LoanService:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self.members = {}
        self.active = {}  # book_id -> (member_id, due date)
        # Every checkout/return: {"kind", "date", "book_id", "member_id", "fine_cents"}
        self.events = []

    def add_member(self, member: Member) -> None:
        self.members[member.id] = member

    def checkout(self, book_id: str, member_id: str, on: dt.date) -> dt.date:
        self.catalog.get(book_id)
        if member_id not in self.members:
            raise LoanError(f"unknown member {member_id}")
        if book_id in self.active:
            raise LoanError(f"{book_id} is already checked out")
        due = on + dt.timedelta(days=LOAN_DAYS)
        self.active[book_id] = (member_id, due)
        self.events.append({"kind": "checkout", "date": on, "book_id": book_id, "member_id": member_id, "fine_cents": 0})
        return due

    def return_book(self, book_id: str, on: dt.date) -> int:
        if book_id not in self.active:
            raise LoanError(f"{book_id} is not checked out")
        member_id, _due = self.active.pop(book_id)
        self.events.append({"kind": "return", "date": on, "book_id": book_id, "member_id": member_id, "fine_cents": 0})
        return 0

    def due_date(self, book_id: str) -> dt.date:
        return self.active[book_id][1]
