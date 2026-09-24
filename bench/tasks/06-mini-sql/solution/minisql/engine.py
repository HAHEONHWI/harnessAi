"""In-memory SQL engine (reference solution)."""
import re


class SQLError(Exception):
    pass


KEYWORDS = {
    "SELECT", "DISTINCT", "FROM", "AS", "JOIN", "INNER", "ON", "WHERE", "GROUP", "BY", "HAVING", "ORDER", "ASC",
    "DESC", "LIMIT", "OFFSET", "AND", "OR", "NOT", "IS", "NULL", "LIKE", "IN", "BETWEEN", "CREATE", "TABLE",
    "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE",
}
AGGREGATES = {"COUNT", "SUM", "AVG", "MIN", "MAX"}
TYPES = {"INTEGER", "REAL", "TEXT"}
TOKEN_RE = re.compile(r"""\s*(?:
    (?P<real>\d+\.\d*|\.\d+)
   |(?P<int>\d+)
   |(?P<str>'(?:[^']|'')*')
   |(?P<word>[A-Za-z_][A-Za-z0-9_]*)
   |(?P<op>!=|<>|<=|>=|\|\||[-+*/%=<>(),.;])
)""", re.X)


def tokenize(sql):
    tokens, pos = [], 0
    while True:
        while pos < len(sql) and sql[pos].isspace():
            pos += 1
        if pos >= len(sql):
            break
        m = TOKEN_RE.match(sql, pos)
        if not m or m.end() == pos:
            raise SQLError(f"unexpected character at {pos}: {sql[pos:pos + 10]!r}")
        kind, text = m.lastgroup, m.group(m.lastgroup)
        if kind == "real":
            tokens.append(("num", float(text)))
        elif kind == "int":
            tokens.append(("num", int(text)))
        elif kind == "str":
            tokens.append(("str", text[1:-1].replace("''", "'")))
        elif kind == "word":
            upper = text.upper()
            tokens.append(("kw", upper) if upper in KEYWORDS else ("id", text.lower()))
        else:
            tokens.append(("op", text))
        pos = m.end()
    while tokens and tokens[-1] == ("op", ";"):
        tokens.pop()
    tokens.append(("end", None))
    return tokens


class Parser:
    def __init__(self, sql):
        self.t = tokenize(sql)
        self.i = 0

    def peek(self, offset=0):
        return self.t[min(self.i + offset, len(self.t) - 1)]

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def accept(self, kind, value=None):
        tok = self.peek()
        if tok[0] == kind and (value is None or tok[1] == value):
            self.i += 1
            return tok
        return None

    def expect(self, kind, value=None):
        tok = self.accept(kind, value)
        if not tok:
            raise SQLError(f"expected {value or kind}, got {self.peek()[1]!r}")
        return tok

    def kw(self, *words):
        return self.peek()[0] == "kw" and self.peek()[1] in words

    def ident(self):
        return self.expect("id")[1]

    # statements
    def statement(self):
        if self.accept("kw", "CREATE"):
            node = self.create()
        elif self.accept("kw", "INSERT"):
            node = self.insert()
        elif self.accept("kw", "SELECT"):
            node = self.select()
        elif self.accept("kw", "UPDATE"):
            node = self.update()
        elif self.accept("kw", "DELETE"):
            node = self.delete()
        else:
            raise SQLError(f"unknown statement {self.peek()[1]!r}")
        self.expect("end")
        return node

    def create(self):
        self.expect("kw", "TABLE")
        name = self.ident()
        self.expect("op", "(")
        cols = []
        while True:
            col = self.ident()
            typ = self.next()
            if typ[0] != "id" or typ[1].upper() not in TYPES:
                raise SQLError(f"bad type for {col}")
            cols.append((col, typ[1].upper()))
            if not self.accept("op", ","):
                break
        self.expect("op", ")")
        return ("create", name, cols)

    def insert(self):
        self.expect("kw", "INTO")
        name = self.ident()
        cols = None
        if self.accept("op", "("):
            cols = [self.ident()]
            while self.accept("op", ","):
                cols.append(self.ident())
            self.expect("op", ")")
        self.expect("kw", "VALUES")
        rows = []
        while True:
            self.expect("op", "(")
            row = [self.expr()]
            while self.accept("op", ","):
                row.append(self.expr())
            self.expect("op", ")")
            rows.append(row)
            if not self.accept("op", ","):
                break
        return ("insert", name, cols, rows)

    def update(self):
        name = self.ident()
        self.expect("kw", "SET")
        sets = []
        while True:
            col = self.ident()
            self.expect("op", "=")
            sets.append((col, self.expr()))
            if not self.accept("op", ","):
                break
        where = self.expr() if self.accept("kw", "WHERE") else None
        return ("update", name, sets, where)

    def delete(self):
        self.expect("kw", "FROM")
        name = self.ident()
        where = self.expr() if self.accept("kw", "WHERE") else None
        return ("delete", name, where)

    def table_ref(self):
        name = self.ident()
        alias = name
        if self.accept("kw", "AS"):
            alias = self.ident()
        elif self.peek()[0] == "id":
            alias = self.ident()
        return name, alias

    def select(self):
        q = {"distinct": bool(self.accept("kw", "DISTINCT")), "items": [], "join": None, "where": None,
             "group": [], "having": None, "order": [], "limit": None, "offset": 0}
        if self.accept("op", "*"):
            q["items"] = None
        else:
            while True:
                e = self.expr()
                alias = None
                if self.accept("kw", "AS"):
                    alias = self.ident()
                elif self.peek()[0] == "id":
                    alias = self.ident()
                q["items"].append((e, alias))
                if not self.accept("op", ","):
                    break
        self.expect("kw", "FROM")
        q["from"] = self.table_ref()
        if self.kw("JOIN", "INNER"):
            self.accept("kw", "INNER")
            self.expect("kw", "JOIN")
            table = self.table_ref()
            self.expect("kw", "ON")
            q["join"] = (table, self.expr())
        if self.accept("kw", "WHERE"):
            q["where"] = self.expr()
        if self.accept("kw", "GROUP"):
            self.expect("kw", "BY")
            q["group"].append(self.expr())
            while self.accept("op", ","):
                q["group"].append(self.expr())
        if self.accept("kw", "HAVING"):
            q["having"] = self.expr()
        if self.accept("kw", "ORDER"):
            self.expect("kw", "BY")
            while True:
                e = self.expr()
                desc = bool(self.accept("kw", "DESC"))
                if not desc:
                    self.accept("kw", "ASC")
                q["order"].append((e, desc))
                if not self.accept("op", ","):
                    break
        if self.accept("kw", "LIMIT"):
            q["limit"] = self.int_literal()
            if self.accept("kw", "OFFSET"):
                q["offset"] = self.int_literal()
        return ("select", q)

    def int_literal(self):
        tok = self.expect("num")
        if not isinstance(tok[1], int):
            raise SQLError("LIMIT/OFFSET must be integers")
        return tok[1]

    # expressions
    def expr(self):
        return self.or_()

    def or_(self):
        node = self.and_()
        while self.accept("kw", "OR"):
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while self.accept("kw", "AND"):
            node = ("and", node, self.not_())
        return node

    def not_(self):
        if self.accept("kw", "NOT"):
            return ("not", self.not_())
        return self.predicate()

    def predicate(self):
        left = self.concat()
        while True:
            tok = self.peek()
            if tok[0] == "op" and tok[1] in ("=", "!=", "<>", "<", "<=", ">", ">="):
                self.next()
                op = "!=" if tok[1] == "<>" else tok[1]
                left = ("cmp", op, left, self.concat())
                continue
            if self.accept("kw", "IS"):
                negate = bool(self.accept("kw", "NOT"))
                self.expect("kw", "NULL")
                left = ("isnull", left, negate)
                continue
            negate = False
            if self.kw("NOT") and self.peek(1)[0] == "kw" and self.peek(1)[1] in ("LIKE", "IN", "BETWEEN"):
                self.next()
                negate = True
            if self.accept("kw", "LIKE"):
                left = ("like", left, self.concat(), negate)
            elif self.accept("kw", "IN"):
                self.expect("op", "(")
                items = [self.expr()]
                while self.accept("op", ","):
                    items.append(self.expr())
                self.expect("op", ")")
                left = ("in", left, items, negate)
            elif self.accept("kw", "BETWEEN"):
                low = self.concat()
                self.expect("kw", "AND")
                left = ("between", left, low, self.concat(), negate)
            elif negate:
                raise SQLError("dangling NOT")
            else:
                return left

    def concat(self):
        node = self.additive()
        while self.accept("op", "||"):
            node = ("concat", node, self.additive())
        return node

    def additive(self):
        node = self.term()
        while self.peek()[0] == "op" and self.peek()[1] in ("+", "-"):
            node = ("arith", self.next()[1], node, self.term())
        return node

    def term(self):
        node = self.unary()
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/", "%"):
            node = ("arith", self.next()[1], node, self.unary())
        return node

    def unary(self):
        if self.accept("op", "-"):
            return ("neg", self.unary())
        if self.accept("op", "+"):
            return self.unary()
        return self.primary()

    def primary(self):
        tok = self.next()
        kind, value = tok
        if kind in ("num", "str"):
            return ("lit", value)
        if tok == ("kw", "NULL"):
            return ("lit", None)
        if tok == ("op", "("):
            node = self.expr()
            self.expect("op", ")")
            return node
        if kind == "id":
            if self.accept("op", "("):
                name = value.upper()
                if name not in AGGREGATES:
                    raise SQLError(f"unknown function {value}")
                if name == "COUNT" and self.accept("op", "*"):
                    arg = None
                else:
                    arg = self.expr()
                self.expect("op", ")")
                return ("agg", name, arg)
            if self.accept("op", "."):
                return ("col", value, self.ident())
            return ("col", None, value)
        raise SQLError(f"unexpected {value!r}")


def has_agg(node):
    if not isinstance(node, tuple):
        return False
    if node[0] == "agg":
        return True
    return any(has_agg(x) for x in node[1:] if isinstance(x, tuple)) or any(
        has_agg(y) for x in node[1:] if isinstance(x, list) for y in x)


def text_of(v):
    return repr(v) if isinstance(v, float) else str(v)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def compare_values(op, a, b):
    if a is None or b is None:
        return None
    if is_num(a) != is_num(b):
        raise SQLError("cannot compare number with text")
    return {"=": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def like(value, pattern):
    regex = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in pattern)
    return re.fullmatch(regex, value, re.S | re.I) is not None


def coerce(value, typ, col):
    if value is None:
        return None
    if typ == "INTEGER" and isinstance(value, int) and not isinstance(value, bool):
        return value
    if typ == "REAL" and is_num(value):
        return float(value)
    if typ == "TEXT" and isinstance(value, str):
        return value
    raise SQLError(f"{value!r} does not fit {col} {typ}")


class Scope:
    """Resolves column references for one joined row (or a group of rows)."""

    def __init__(self, tables):
        self.tables = tables  # [(alias, [column names])]

    def locate(self, table, col):
        hits = []
        offset = 0
        for alias, cols in self.tables:
            if table is None or table == alias:
                if col in cols:
                    hits.append(offset + cols.index(col))
            offset += len(cols)
        if table is not None and table not in [a for a, _ in self.tables]:
            raise SQLError(f"unknown table {table}")
        if not hits:
            raise SQLError(f"unknown column {col}")
        if len(hits) > 1:
            raise SQLError(f"ambiguous column {col}")
        return hits[0]


class Evaluator:
    def __init__(self, scope):
        self.scope = scope

    def eval(self, node, row, group=None):
        kind = node[0]
        if kind == "lit":
            return node[1]
        if kind == "col":
            return row[self.scope.locate(node[1], node[2])] if row is not None else None
        if kind == "agg":
            if group is None:
                raise SQLError("aggregate not allowed here")
            return self.aggregate(node, group)
        if kind == "neg":
            v = self.eval(node[1], row, group)
            if v is None:
                return None
            if not is_num(v):
                raise SQLError("cannot negate text")
            return -v
        if kind == "arith":
            a, b = self.eval(node[2], row, group), self.eval(node[3], row, group)
            if a is None or b is None:
                return None
            if not (is_num(a) and is_num(b)):
                raise SQLError("arithmetic on text")
            op = node[1]
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if b == 0:
                return None
            if isinstance(a, int) and isinstance(b, int):
                q = abs(a) // abs(b) * (1 if (a >= 0) == (b >= 0) else -1)
                return q if op == "/" else a - q * b
            if op == "/":
                return a / b
            import math
            return math.fmod(a, b)
        if kind == "concat":
            a, b = self.eval(node[1], row, group), self.eval(node[2], row, group)
            return None if a is None or b is None else text_of(a) + text_of(b)
        if kind == "cmp":
            return compare_values(node[1], self.eval(node[2], row, group), self.eval(node[3], row, group))
        if kind == "isnull":
            v = self.eval(node[1], row, group)
            return (v is not None) if node[2] else (v is None)
        if kind == "like":
            v, p = self.eval(node[1], row, group), self.eval(node[2], row, group)
            if v is None or p is None:
                return None
            result = like(text_of(v), text_of(p))
            return not result if node[3] else result
        if kind == "in":
            v = self.eval(node[1], row, group)
            if v is None:
                return None
            saw_null = False
            for item in node[2]:
                x = self.eval(item, row, group)
                if x is None:
                    saw_null = True
                elif compare_values("=", v, x):
                    return not node[3]
            return None if saw_null else node[3]
        if kind == "between":
            v, lo, hi = (self.eval(n, row, group) for n in node[1:4])
            a, b = compare_values(">=", v, lo), compare_values("<=", v, hi)
            result = tri_and(a, b)
            return None if result is None else (not result if node[4] else result)
        if kind == "and":
            return tri_and(self.eval(node[1], row, group), self.eval(node[2], row, group))
        if kind == "or":
            a, b = self.eval(node[1], row, group), self.eval(node[2], row, group)
            if a is True or b is True:
                return True
            if a is None or b is None:
                return None
            return bool(a) or bool(b)
        if kind == "not":
            v = self.eval(node[1], row, group)
            return None if v is None else not v
        raise SQLError(f"bad expression {kind}")

    def aggregate(self, node, group):
        name, arg = node[1], node[2]
        if arg is None:
            return len(group)
        if has_agg(arg):
            raise SQLError("nested aggregate")
        values = [v for v in (self.eval(arg, r) for r in group) if v is not None]
        if name == "COUNT":
            return len(values)
        if not values:
            return None
        if name in ("SUM", "AVG") and not all(is_num(v) for v in values):
            raise SQLError(f"{name} of text")
        if name == "SUM":
            return sum(values)
        if name == "AVG":
            return float(sum(values)) / len(values)
        return min(values) if name == "MIN" else max(values)


def tri_and(a, b):
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return bool(a) and bool(b)


class Database:
    def __init__(self):
        self.tables = {}  # name -> {"columns": [(name, type)], "rows": [list]}

    def table(self, name):
        if name not in self.tables:
            raise SQLError(f"unknown table {name}")
        return self.tables[name]

    def execute(self, sql):
        node = Parser(sql).statement()
        return getattr(self, "_" + node[0])(*node[1:])

    def _create(self, name, cols):
        if name in self.tables:
            raise SQLError(f"table {name} exists")
        if len({c for c, _ in cols}) != len(cols):
            raise SQLError("duplicate column")
        self.tables[name] = {"columns": cols, "rows": []}
        return None

    def _insert(self, name, cols, rows):
        table = self.table(name)
        names = [c for c, _ in table["columns"]]
        targets = cols or names
        for c in targets:
            if c not in names:
                raise SQLError(f"unknown column {c}")
        ev = Evaluator(Scope([]))
        new_rows = []
        for values in rows:
            if len(values) != len(targets):
                raise SQLError("wrong number of values")
            row = [None] * len(names)
            for col, expr in zip(targets, values):
                i = names.index(col)
                row[i] = coerce(ev.eval(expr, None), table["columns"][i][1], col)
            new_rows.append(row)
        table["rows"].extend(new_rows)
        return len(new_rows)

    def _matching(self, name, where):
        table = self.table(name)
        scope = Scope([(name, [c for c, _ in table["columns"]])])
        ev = Evaluator(scope)
        if where is not None and has_agg(where):
            raise SQLError("aggregate in WHERE")
        return table, scope, ev, [r for r in table["rows"] if where is None or ev.eval(where, r) is True]

    def _update(self, name, sets, where):
        table, scope, ev, rows = self._matching(name, where)
        names = [c for c, _ in table["columns"]]
        planned = []
        for row in rows:
            changes = []
            for col, expr in sets:
                if col not in names:
                    raise SQLError(f"unknown column {col}")
                i = names.index(col)
                changes.append((i, coerce(ev.eval(expr, row), table["columns"][i][1], col)))
            planned.append((row, changes))
        for row, changes in planned:
            for i, v in changes:
                row[i] = v
        return len(rows)

    def _delete(self, name, where):
        table, _, _, rows = self._matching(name, where)
        doomed = {id(r) for r in rows}
        table["rows"] = [r for r in table["rows"] if id(r) not in doomed]
        return len(rows)

    def _select(self, q):
        name, alias = q["from"]
        left = self.table(name)
        tables = [(alias, [c for c, _ in left["columns"]])]
        rows = [list(r) for r in left["rows"]]
        if q["join"]:
            (jname, jalias), on = q["join"]
            right = self.table(jname)
            if jalias == alias:
                raise SQLError("duplicate table alias")
            tables.append((jalias, [c for c, _ in right["columns"]]))
            ev = Evaluator(Scope(tables))
            rows = [l + list(r) for l in rows for r in right["rows"] if ev.eval(on, l + list(r)) is True]
        scope = Scope(tables)
        ev = Evaluator(scope)
        if q["where"] is not None:
            if has_agg(q["where"]):
                raise SQLError("aggregate in WHERE")
            rows = [r for r in rows if ev.eval(q["where"], r) is True]
        if q["items"] is None:
            items = []
            for t_alias, cols in tables:
                items += [(("col", t_alias, c), c) for c in cols]
        else:
            items = q["items"]
        # validate column references even when there are no rows
        self._check(items, q, scope)
        aggregate_query = bool(q["group"]) or any(has_agg(e) for e, _ in items) or \
            (q["having"] is not None) or any(has_agg(e) for e, _ in q["order"])
        if aggregate_query:
            groups = {}
            order = []
            for r in rows:
                key = tuple(ev.eval(g, r) for g in q["group"])
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(r)
            units = [(groups[k][0], groups[k]) for k in order]
            if not q["group"] and not units:
                units = [(None, [])]
            if q["having"] is not None:
                units = [u for u in units if ev.eval(q["having"], u[0], u[1]) is True]
        else:
            units = [(r, None) for r in rows]
        out = [(tuple(ev.eval(e, row, group) for e, _ in items), row, group) for row, group in units]
        aliases = [a for _, a in items]
        for expr, desc in reversed(q["order"]):
            if expr[0] == "col" and expr[1] is None and expr[2] in aliases:
                idx = aliases.index(expr[2])
                keyf = lambda o, idx=idx: o[0][idx]
            else:
                keyf = lambda o, expr=expr: ev.eval(expr, o[1], o[2])
            keyed = [(keyf(o), o) for o in out]
            nulls = [o for k, o in keyed if k is None]
            present = [(k, o) for k, o in keyed if k is not None]
            if len({is_num(k) for k, _ in present}) > 1:
                raise SQLError("cannot order numbers and text together")
            present.sort(key=lambda ko: ko[0], reverse=desc)
            out = (nulls + [o for _, o in present]) if not desc else ([o for _, o in present] + nulls)
        result = [o[0] for o in out]
        if q["distinct"]:
            seen, unique = set(), []
            for r in result:
                if r not in seen:
                    seen.add(r)
                    unique.append(r)
            result = unique
        if q["limit"] is not None:
            result = result[q["offset"]:q["offset"] + q["limit"]]
        elif q["offset"]:
            result = result[q["offset"]:]
        return result

    def _check(self, items, q, scope):
        def walk(node):
            if not isinstance(node, tuple):
                return
            if node[0] == "col":
                scope.locate(node[1], node[2])
                return
            for x in node[1:]:
                if isinstance(x, tuple):
                    walk(x)
                elif isinstance(x, list):
                    for y in x:
                        walk(y)
        aliases = [a for _, a in items]
        for e, _ in items:
            walk(e)
        for g in q["group"]:
            walk(g)
        if q["having"] is not None:
            walk(q["having"])
        for e, _ in q["order"]:
            if not (e[0] == "col" and e[1] is None and e[2] in aliases):
                walk(e)
