from dataclasses import dataclass


@dataclass
class Book:
    id: str
    title: str
    author: str
    year: int


@dataclass
class Member:
    id: str
    name: str
    fines_cents: int = 0
