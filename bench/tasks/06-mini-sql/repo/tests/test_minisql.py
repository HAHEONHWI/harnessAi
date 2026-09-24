import unittest

from minisql import Database


class MiniSqlTests(unittest.TestCase):
    def test_create_insert_select_all(self):
        db = Database()
        db.execute("CREATE TABLE users (id INTEGER, name TEXT)")
        db.execute("INSERT INTO users VALUES (1, 'ann')")
        db.execute("INSERT INTO users VALUES (2, 'bob')")
        self.assertEqual(db.execute("SELECT * FROM users"), [(1, "ann"), (2, "bob")])

    def test_where(self):
        db = Database()
        db.execute("CREATE TABLE t (x INTEGER)")
        db.execute("INSERT INTO t VALUES (1), (2), (3)")
        self.assertEqual(db.execute("SELECT x FROM t WHERE x >= 2"), [(2,), (3,)])


if __name__ == "__main__":
    unittest.main()
