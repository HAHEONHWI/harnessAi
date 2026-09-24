import unittest

from minisql import Database, SQLError


def company():
    db = Database()
    db.execute("CREATE TABLE emp (id INTEGER, name TEXT, dept TEXT, salary REAL, boss INTEGER)")
    db.execute("INSERT INTO emp VALUES (1, 'Ann', 'eng', 100, NULL), (2, 'bob', 'eng', 80, 1), "
               "(3, 'Cy', 'ops', 60, 1), (4, 'Di', NULL, NULL, 2), (5, 'Ed', 'ops', 60.5, 3)")
    db.execute("CREATE TABLE dept (code TEXT, title TEXT)")
    db.execute("INSERT INTO dept VALUES ('eng', 'Engineering'), ('ops', 'Operations'), ('hr', 'People')")
    return db


class Statements(unittest.TestCase):
    def test_return_values(self):
        db = Database()
        self.assertIsNone(db.execute("create table T (A integer, b text);"))
        self.assertEqual(db.execute("INSERT INTO t VALUES (1, 'x'), (2, 'y')"), 2)
        self.assertEqual(db.execute("UPDATE t SET b = 'z' WHERE a = 2"), 1)
        self.assertEqual(db.execute("DELETE FROM t WHERE a = 1"), 1)
        self.assertEqual(db.execute("SELECT * FROM T"), [(2, "z")])

    def test_insert_columns_and_types(self):
        db = Database()
        db.execute("CREATE TABLE t (a INTEGER, b REAL, c TEXT)")
        db.execute("INSERT INTO t (c, a) VALUES ('hi', 3)")
        db.execute("INSERT INTO t VALUES (NULL, 2, NULL)")
        rows = db.execute("SELECT * FROM t")
        self.assertEqual(rows, [(3, None, "hi"), (None, 2.0, None)])
        self.assertIsInstance(rows[1][1], float)

    def test_bad_inserts_are_atomic(self):
        db = Database()
        db.execute("CREATE TABLE t (a INTEGER, c TEXT)")
        for sql in ("INSERT INTO t VALUES (1, 'ok'), ('x', 'bad')", "INSERT INTO t VALUES (1.5, 'x')",
                    "INSERT INTO t VALUES (1)", "INSERT INTO t (a, zz) VALUES (1, 2)", "INSERT INTO t VALUES (1, 2)"):
            with self.assertRaises(SQLError, msg=sql):
                db.execute(sql)
        self.assertEqual(db.execute("SELECT * FROM t"), [])

    def test_errors(self):
        db = company()
        for sql in ("CREATE TABLE emp (x INTEGER)", "SELECT * FROM nope", "SELECT nope FROM emp",
                    "SELECT * FROM emp WHERE", "SELEC * FROM emp", "SELECT name FROM emp WHERE name = 1",
                    "SELECT x.name FROM emp", "SELECT name FROM emp WHERE COUNT(*) > 1",
                    "UPDATE emp SET salary = 'lots'"):
            with self.assertRaises(SQLError, msg=sql):
                db.execute(sql)

    def test_update_uses_old_row_values(self):
        db = Database()
        db.execute("CREATE TABLE t (a INTEGER, b INTEGER)")
        db.execute("INSERT INTO t VALUES (1, 2)")
        db.execute("UPDATE t SET a = b, b = a")
        self.assertEqual(db.execute("SELECT * FROM t"), [(2, 1)])


class Expressions(unittest.TestCase):
    def one(self, expr):
        db = Database()
        db.execute("CREATE TABLE one (x INTEGER)")
        db.execute("INSERT INTO one VALUES (1)")
        return db.execute(f"SELECT {expr} FROM one")[0][0]

    def test_integer_division_and_modulo(self):
        self.assertEqual([self.one(e) for e in ("7 / 2", "-7 / 2", "7 % 3", "-7 % 3", "7.0 / 2", "1 / 0", "5 % 0")],
                         [3, -3, 1, -1, 3.5, None, None])

    def test_precedence(self):
        self.assertEqual(self.one("2 + 3 * 4 - -1"), 15)
        self.assertEqual(self.one("(2 + 3) * 4"), 20)
        self.assertEqual(self.one("'a' || 1 + 2 || 'b'"), "a3b")
        self.assertEqual(self.one("'v' || 1.5"), "v1.5")

    def test_three_valued_logic(self):
        self.assertIs(self.one("NULL = NULL"), None)
        self.assertIs(self.one("1 = 0 AND NULL = 1"), False)
        self.assertIs(self.one("1 = 1 OR NULL = 1"), True)
        self.assertIs(self.one("NOT (NULL = 1)"), None)
        self.assertIs(self.one("NULL IS NULL"), True)
        self.assertIs(self.one("1 IS NOT NULL"), True)
        self.assertIsNone(self.one("NULL + 1"))

    def test_like_in_between(self):
        self.assertIs(self.one("'Hello' LIKE 'h_l%'"), True)
        self.assertIs(self.one("'Hello' NOT LIKE '%x%'"), True)
        self.assertIs(self.one("3 IN (1, 2, 3)"), True)
        self.assertIs(self.one("4 NOT IN (1, 2)"), True)
        self.assertIsNone(self.one("4 IN (1, NULL)"))
        self.assertIs(self.one("5 BETWEEN 1 AND 5"), True)
        self.assertIs(self.one("0 NOT BETWEEN 1 AND 5"), True)

    def test_string_literals_and_case(self):
        self.assertEqual(self.one("'it''s'"), "it's")
        self.assertIs(self.one("'a' < 'B'"), False)


class Queries(unittest.TestCase):
    def test_where_and_order(self):
        db = company()
        self.assertEqual(db.execute("SELECT name FROM emp WHERE salary >= 60 AND dept = 'ops' ORDER BY salary DESC"),
                         [("Ed",), ("Cy",)])
        self.assertEqual(db.execute("SELECT name FROM emp WHERE dept != 'eng'"), [("Cy",), ("Ed",)])

    def test_order_nulls_stability_alias(self):
        db = company()
        self.assertEqual(db.execute("SELECT name, salary AS pay FROM emp ORDER BY pay"),
                         [("Di", None), ("Cy", 60.0), ("Ed", 60.5), ("bob", 80.0), ("Ann", 100.0)])
        self.assertEqual(db.execute("SELECT name FROM emp ORDER BY salary DESC LIMIT 2 OFFSET 3"), [("Cy",), ("Di",)])
        self.assertEqual(db.execute("SELECT id FROM emp ORDER BY dept, id DESC"), [(4,), (2,), (1,), (5,), (3,)])

    def test_expressions_in_select_and_order(self):
        db = company()
        self.assertEqual(db.execute("SELECT id * 10, name || '!' FROM emp WHERE id <= 2 ORDER BY -id"),
                         [(20, "bob!"), (10, "Ann!")])

    def test_distinct(self):
        db = company()
        self.assertEqual(db.execute("SELECT DISTINCT dept FROM emp"), [("eng",), ("ops",), (None,)])

    def test_join(self):
        db = company()
        self.assertEqual(db.execute("SELECT e.name, d.title FROM emp AS e JOIN dept d ON e.dept = d.code ORDER BY e.id"),
                         [("Ann", "Engineering"), ("bob", "Engineering"), ("Cy", "Operations"), ("Ed", "Operations")])
        self.assertEqual(db.execute("SELECT e.name, b.name FROM emp e INNER JOIN emp b ON e.boss = b.id WHERE b.name = 'Ann'"),
                         [("bob", "Ann"), ("Cy", "Ann")])
        star = db.execute("SELECT * FROM dept JOIN emp ON code = dept WHERE id = 3")
        self.assertEqual(star, [("ops", "Operations", 3, "Cy", "ops", 60.0, 1)])

    def test_ambiguous_column(self):
        db = company()
        with self.assertRaises(SQLError):
            db.execute("SELECT name FROM emp e JOIN emp b ON e.boss = b.id")

    def test_group_by(self):
        db = company()
        self.assertEqual(db.execute("SELECT dept, COUNT(*), COUNT(salary), SUM(salary), AVG(salary), MIN(name), MAX(salary) "
                                    "FROM emp GROUP BY dept ORDER BY dept"),
                         [(None, 1, 0, None, None, "Di", None), ("eng", 2, 2, 180.0, 90.0, "Ann", 100.0),
                          ("ops", 2, 2, 120.5, 60.25, "Cy", 60.5)])

    def test_having_and_order_by_aggregate(self):
        db = company()
        self.assertEqual(db.execute("SELECT dept, COUNT(*) AS n FROM emp WHERE dept IS NOT NULL GROUP BY dept "
                                    "HAVING SUM(salary) > 150 ORDER BY n"), [("eng", 2)])
        self.assertEqual(db.execute("SELECT dept FROM emp WHERE dept IS NOT NULL GROUP BY dept ORDER BY MAX(salary)"),
                         [("ops",), ("eng",)])

    def test_aggregate_without_group(self):
        db = company()
        self.assertEqual(db.execute("SELECT COUNT(*), AVG(id) FROM emp"), [(5, 3.0)])
        self.assertEqual(db.execute("SELECT COUNT(*), SUM(salary), MAX(name) FROM emp WHERE id > 99"), [(0, None, None)])
        self.assertIsInstance(db.execute("SELECT AVG(id) FROM emp")[0][0], float)

    def test_group_by_expression(self):
        db = company()
        self.assertEqual(db.execute("SELECT id % 2, COUNT(*) FROM emp GROUP BY id % 2 ORDER BY id % 2"),
                         [(0, 2), (1, 3)])

    def test_update_and_delete_with_where(self):
        db = company()
        self.assertEqual(db.execute("UPDATE emp SET salary = salary * 2 WHERE dept = 'ops'"), 2)
        self.assertEqual(db.execute("SELECT SUM(salary) FROM emp WHERE dept = 'ops'"), [(241.0,)])
        self.assertEqual(db.execute("DELETE FROM emp WHERE salary IS NULL OR name LIKE 'a%'"), 2)
        self.assertEqual(db.execute("SELECT id FROM emp"), [(2,), (3,), (5,)])


if __name__ == "__main__":
    unittest.main()
