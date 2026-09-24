"""Spreadsheet engine for the budgeting app."""
import re
from collections import deque
from decimal import ROUND_HALF_UP, Decimal


class Err:
    __slots__ = ("code",)

    def __init__(self, code):
        self.code = code


DIV0, VALUE, NAME, REF, PARSE, CYCLE = (Err(c) for c in ("#DIV/0!", "#VALUE!", "#NAME?", "#REF!", "#PARSE!", "#CYCLE!"))
NUMBER_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
TOKEN_RE = re.compile(r"""\s*(?:
    (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
   |(?P<str>"(?:[^"]|"")*")
   |(?P<ref>\$?[A-Za-z]+\$?\d+)(?![A-Za-z0-9_(])
   |(?P<name>[A-Za-z_][A-Za-z0-9_.]*)
   |(?P<op><>|<=|>=|[-+*/^&=<>(),:])
)""", re.X)
REF_PARTS = re.compile(r"\$?([A-Za-z]+)\$?(\d+)$")
COMPARE = ("=", "<>", "<", "<=", ">", ">=")


class ParseError(Exception):
    pass


def col_number(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def col_letters(n):
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def parse_ref(text):
    """(col, row) or None when outside A1..ZZ9999."""
    m = REF_PARTS.match(text)
    if not m:
        return None
    letters, row = m.group(1).upper(), int(m.group(2))
    if len(letters) > 2 or not 1 <= row <= 9999:
        return None
    return col_number(letters), row


def norm(cell):
    ref = parse_ref(cell.strip())
    if ref is None:
        raise KeyError(cell)
    return f"{col_letters(ref[0])}{ref[1]}"


def tokenize(text):
    tokens, pos = [], 0
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            return tokens
        m = TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            raise ParseError(text[pos:])
        kind = m.lastgroup
        tokens.append((kind, m.group(kind)))
        pos = m.end()


class Parser:
    def __init__(self, tokens):
        self.tokens, self.i = tokens, 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def take(self, value=None):
        tok = self.peek()
        if tok[0] is None or (value is not None and tok[1] != value):
            raise ParseError(value)
        self.i += 1
        return tok

    def parse(self):
        node = self.comparison()
        if self.i != len(self.tokens):
            raise ParseError("trailing")
        return node

    def binary(self, ops, lower):
        node = lower()
        while self.peek()[0] == "op" and self.peek()[1] in ops:
            op = self.take()[1]
            node = ("bin", op, node, lower())
        return node

    def comparison(self):
        return self.binary(COMPARE, self.concat)

    def concat(self):
        return self.binary(("&",), self.additive)

    def additive(self):
        return self.binary(("+", "-"), self.term)

    def term(self):
        return self.binary(("*", "/"), self.power)

    def power(self):
        return self.binary(("^",), self.unary)

    def unary(self):
        kind, value = self.peek()
        if kind == "op" and value in ("+", "-"):
            self.take()
            return ("neg" if value == "-" else "pos", self.unary())
        return self.primary()

    def primary(self):
        kind, value = self.take()
        if kind == "num":
            return ("val", float(value))
        if kind == "str":
            return ("val", value[1:-1].replace('""', '"'))
        if kind == "ref":
            start = parse_ref(value)
            if self.peek() == ("op", ":"):
                self.take()
                end_kind, end_value = self.take()
                if end_kind != "ref":
                    raise ParseError(":")
                end = parse_ref(end_value)
                if start is None or end is None:
                    return ("err", REF)
                cells = [f"{col_letters(c)}{r}"
                         for c in range(min(start[0], end[0]), max(start[0], end[0]) + 1)
                         for r in range(min(start[1], end[1]), max(start[1], end[1]) + 1)]
                return ("range", cells)
            if start is None:
                return ("err", REF)
            return ("ref", f"{col_letters(start[0])}{start[1]}")
        if kind == "name":
            upper = value.upper()
            if self.peek() == ("op", "("):
                self.take()
                args = []
                if self.peek() != ("op", ")"):
                    args.append(self.comparison())
                    while self.peek() == ("op", ","):
                        self.take()
                        args.append(self.comparison())
                self.take(")")
                return ("call", upper, args)
            if upper in ("TRUE", "FALSE"):
                return ("val", upper == "TRUE")
            raise ParseError(value)
        if (kind, value) == ("op", "("):
            node = self.comparison()
            self.take(")")
            return node
        raise ParseError(value)


def deps_of(node, out):
    kind = node[0]
    if kind == "ref":
        out.add(node[1])
    elif kind == "range":
        out.update(node[1])
    elif kind in ("neg", "pos"):
        deps_of(node[1], out)
    elif kind == "bin":
        deps_of(node[2], out)
        deps_of(node[3], out)
    elif kind == "call":
        for arg in node[2]:
            deps_of(arg, out)
    return out


def to_num(v):
    if v is None:
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    return VALUE


def to_text(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (int, float)):
        return format(v, ".15g")
    return v


def compare(op, a, b):
    if isinstance(a, str) or isinstance(b, str):
        if not all(x is None or isinstance(x, str) for x in (a, b)):
            return VALUE
        a, b = (a or "").lower(), (b or "").lower()
    else:
        a, b = to_num(a), to_num(b)
    return {"=": a == b, "<>": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


class Sheet:
    def __init__(self):
        self._raw = {}
        self._parsed = {}
        self._values = {}
        self._dirty = False

    def set(self, cell, text):
        cell = norm(cell)
        text = "" if text is None else str(text)
        self._raw[cell] = text
        self._parsed.pop(cell, None)
        if text.startswith("="):
            try:
                self._parsed[cell] = Parser(tokenize(text[1:])).parse()
            except ParseError:
                self._parsed[cell] = ("err", PARSE)
        self._dirty = True

    def formula(self, cell):
        return self._raw.get(norm(cell), "")

    def get(self, cell):
        cell = norm(cell)
        if self._dirty:
            self._recalc()
        value = self._value(cell)
        return value.code if isinstance(value, Err) else value

    def _literal(self, cell):
        text = self._raw.get(cell, "")
        if text == "":
            return None
        stripped = text.strip()
        if NUMBER_RE.match(stripped):
            number = float(stripped)
            return int(number) if number.is_integer() and "." not in stripped and "e" not in stripped.lower() else number
        return text

    def _value(self, cell):
        if cell in self._parsed:
            return self._values.get(cell, CYCLE)
        return self._literal(cell)

    def _recalc(self):
        deps = {cell: deps_of(node, set()) & self._parsed.keys() for cell, node in self._parsed.items()}
        pending = {cell: len(d) for cell, d in deps.items()}
        users = {}
        for cell, d in deps.items():
            for dep in d:
                users.setdefault(dep, []).append(cell)
        queue = deque(cell for cell, n in pending.items() if n == 0)
        self._values = {}
        while queue:
            cell = queue.popleft()
            self._values[cell] = self._eval(self._parsed[cell])
            for user in users.get(cell, []):
                pending[user] -= 1
                if pending[user] == 0:
                    queue.append(user)
        self._dirty = False

    def _eval(self, node):
        kind = node[0]
        if kind == "val":
            return node[1]
        if kind == "err":
            return node[1]
        if kind == "ref":
            return self._value(node[1])
        if kind == "range":
            return VALUE
        if kind in ("neg", "pos"):
            v = self._eval(node[1])
            if isinstance(v, Err):
                return v
            n = to_num(v)
            if isinstance(n, Err):
                return n
            return -n if kind == "neg" else n
        if kind == "bin":
            return self._binary(node[1], node[2], node[3])
        return self._call(node[1], node[2])

    def _binary(self, op, left, right):
        a = self._eval(left)
        if isinstance(a, Err):
            return a
        b = self._eval(right)
        if isinstance(b, Err):
            return b
        if op in COMPARE:
            return compare(op, a, b)
        if op == "&":
            return to_text(a) + to_text(b)
        x, y = to_num(a), to_num(b)
        for v in (x, y):
            if isinstance(v, Err):
                return v
        try:
            if op == "+":
                return x + y
            if op == "-":
                return x - y
            if op == "*":
                return x * y
            if op == "/":
                return DIV0 if y == 0 else x / y
            result = float(x) ** float(y)
            return VALUE if isinstance(result, complex) else result
        except ZeroDivisionError:
            return DIV0
        except (OverflowError, ValueError):
            return VALUE

    def _numbers(self, args, count_mode=False):
        values = []
        for arg in args:
            if arg[0] == "range":
                for cell in arg[1]:
                    v = self._value(cell)
                    if isinstance(v, Err):
                        return v
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        values.append(v)
                continue
            v = self._eval(arg)
            if isinstance(v, Err):
                return v
            if v is None:
                continue
            if isinstance(v, str):
                return VALUE
            values.append(int(v) if isinstance(v, bool) else v)
        return values

    def _call(self, name, args):
        arity = {"IF": (2, 3), "ROUND": (2, 2), "LEN": (1, 1)}
        aggregates = ("SUM", "MIN", "MAX", "AVERAGE", "COUNT")
        if name not in arity and name not in aggregates and name != "CONCAT":
            return NAME
        low, high = arity.get(name, (1, 255))
        if not low <= len(args) <= high:
            return NAME
        if name in aggregates:
            nums = self._numbers(args)
            if isinstance(nums, Err):
                return nums
            if name == "SUM":
                return sum(nums)
            if name == "MIN":
                return min(nums) if nums else 0
            if name == "MAX":
                return max(nums) if nums else 0
            if name == "COUNT":
                return len(nums)
            return sum(nums) / len(nums) if nums else DIV0
        if name == "IF":
            cond = self._eval(args[0])
            if isinstance(cond, Err):
                return cond
            if isinstance(cond, str):
                return VALUE
            if to_num(cond) != 0:
                return self._eval(args[1])
            return self._eval(args[2]) if len(args) == 3 else False
        if name == "CONCAT":
            parts = []
            for arg in args:
                items = [self._value(c) for c in arg[1]] if arg[0] == "range" else [self._eval(arg)]
                for v in items:
                    if isinstance(v, Err):
                        return v
                    parts.append(to_text(v))
            return "".join(parts)
        values = [self._eval(a) for a in args]
        for v in values:
            if isinstance(v, Err):
                return v
        if name == "LEN":
            return len(to_text(values[0]))
        x, digits = to_num(values[0]), to_num(values[1])
        for v in (x, digits):
            if isinstance(v, Err):
                return v
        quantum = Decimal(1).scaleb(-int(digits))
        return float(Decimal(str(x)).quantize(quantum, rounding=ROUND_HALF_UP))
