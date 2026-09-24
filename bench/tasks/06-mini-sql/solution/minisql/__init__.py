"""Tiny in-memory database used by the reporting prototype."""
from .engine import Database, SQLError

__all__ = ["Database", "SQLError"]
