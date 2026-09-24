from .models import Book


class Catalog:
    def __init__(self):
        self._books = {}

    def add(self, book: Book) -> None:
        if book.id in self._books:
            raise ValueError(f"duplicate book id {book.id}")
        self._books[book.id] = book

    def get(self, book_id: str) -> Book:
        return self._books[book_id]

    def all(self):
        return list(self._books.values())

    def search(self, query: str):
        words = query.lower().split()
        found = [b for b in self._books.values()
                 if all(w in b.title.lower() or w in b.author.lower() for w in words)]
        return sorted(found, key=lambda b: (b.title, b.id))
