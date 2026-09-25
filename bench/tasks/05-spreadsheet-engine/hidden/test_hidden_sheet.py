import time
import unittest

from sheet import Sheet


def sheet(**cells):
    s = Sheet()
    for cell, text in cells.items():
        s.set(cell, text)
    return s


def value(formula, **cells):
    s = sheet(**cells)
    s.set("Z99", formula)
    return s.get("Z99")


class Literals(unittest.TestCase):
    def test_numbers_text_empty(self):
        s = sheet(A1=" -3.5 ", A2="1e3", A3="12abc", A4="", A5="TRUE")
        self.assertEqual(s.get("A1"), -3.5)
        self.assertEqual(s.get("A2"), 1000)
        self.assertEqual(s.get("A3"), "12abc")
        self.assertIsNone(s.get("A4"))
        self.assertEqual(s.get("A5"), "TRUE")
        self.assertIsNone(s.get("B7"))

    def test_case_insensitive_names_and_formula_text(self):
        s = sheet(a1="4", b1="=A1*2")
        self.assertEqual(s.get("B1"), 8)
        self.assertEqual(s.formula("b1"), "=A1*2")
        self.assertEqual(s.formula("C1"), "")


class Operators(unittest.TestCase):
    def test_precedence(self):
        self.assertEqual(value("=1+2*3-4/2"), 5)
        self.assertEqual(value("=(1+2)*3"), 9)
        self.assertEqual(value("=2^3^2"), 64)
        self.assertEqual(value("=-2^2"), 4)
        self.assertEqual(value("=2*-3"), -6)
        self.assertEqual(value("=--5"), 5)

    def test_concat_precedence_and_formatting(self):
        self.assertEqual(value("=1+2&3"), "33")
        self.assertEqual(value("=2.0&\"|\"&2.5&\"|\"&TRUE&\"|\"&A1"), "2|2.5|TRUE|")
        self.assertEqual(value("=0.1+0.2&\"\""), "0.3")

    def test_string_literal_quotes(self):
        self.assertEqual(value('="say ""hi"""'), 'say "hi"')

    def test_comparisons(self):
        self.assertIs(value('="abc"="ABC"'), True)
        self.assertIs(value("=1+1=2"), True)
        self.assertIs(value("=3<>3"), False)
        self.assertIs(value('="b">"a"'), True)
        self.assertIs(value("=A1=0"), True)
        self.assertIs(value('=A1=""'), True)
        self.assertIs(value("=TRUE=1"), True)

    def test_mixed_compare_is_value_error(self):
        self.assertEqual(value('=1<"a"'), "#VALUE!")

    def test_booleans_and_empty_in_arithmetic(self):
        self.assertEqual(value("=TRUE+TRUE+A1"), 2)

    def test_text_in_arithmetic(self):
        self.assertEqual(value('="a"+1'), "#VALUE!")
        self.assertEqual(value("=A1*2", A1="x"), "#VALUE!")


class Functions(unittest.TestCase):
    def cells(self):
        return dict(A1="1", A2="2", A3="x", A4="", A5="=TRUE", B1="10")

    def test_aggregates_skip_non_numbers_in_ranges(self):
        c = self.cells()
        self.assertEqual(value("=SUM(A1:A5)", **c), 3)
        self.assertEqual(value("=COUNT(A1:A5)", **c), 2)
        self.assertEqual(value("=AVERAGE(A1:A5)", **c), 1.5)
        self.assertEqual(value("=MAX(A1:B1)", **c), 10)
        self.assertEqual(value("=MIN(A2:B1)", **c), 1)

    def test_direct_arguments(self):
        self.assertEqual(value("=SUM(1,TRUE,A4,A1:A2)", A1="1", A2="2"), 5)
        self.assertEqual(value('=SUM(1,"2")'), "#VALUE!")
        self.assertEqual(value("=COUNT(1,TRUE,A9)"), 2)

    def test_empty_aggregates(self):
        self.assertEqual(value("=MIN(C1:C3)"), 0)
        self.assertEqual(value("=MAX(C1:C3)"), 0)
        self.assertEqual(value("=AVERAGE(C1:C3)"), "#DIV/0!")

    def test_reversed_range(self):
        self.assertEqual(value("=SUM(B2:A1)", A1="1", A2="2", B1="3", B2="4"), 10)

    def test_if_lazy(self):
        self.assertEqual(value('=IF(1>0,"yes",1/0)'), "yes")
        self.assertEqual(value('=IF(0,1/0,"no")'), "no")
        self.assertIs(value("=IF(A1,1)"), False)
        self.assertEqual(value('=IF("x",1,2)'), "#VALUE!")

    def test_round(self):
        self.assertEqual(value("=ROUND(2.5,0)"), 3)
        self.assertEqual(value("=ROUND(-2.5,0)"), -3)
        self.assertEqual(value("=ROUND(1250,-2)"), 1300)

    def test_len_concat_case_insensitive(self):
        self.assertEqual(value("=len(12.5)"), 4)
        self.assertEqual(value('=Concat("a",1,TRUE,A1)', A1="3"), "a1TRUE3")

    def test_unknown_function_and_arity(self):
        self.assertEqual(value("=FOO(1)"), "#NAME?")
        self.assertEqual(value("=ROUND(1)"), "#NAME?")
        self.assertEqual(value("=IF(1)"), "#NAME?")


class Errors(unittest.TestCase):
    def test_div_zero_and_propagation(self):
        s = sheet(A1="=1/0", A2="=A1+1", A3="=SUM(A1:A2)", A4="=IF(A1,1,2)")
        for cell in ("A1", "A2", "A3", "A4"):
            self.assertEqual(s.get(cell), "#DIV/0!", cell)

    def test_leftmost_error_wins(self):
        self.assertEqual(value('=1/0+("a"+1)'), "#DIV/0!")
        self.assertEqual(value('=("a"+1)+1/0'), "#VALUE!")

    def test_ref_and_parse(self):
        self.assertEqual(value("=A0+1"), "#REF!")
        self.assertEqual(value("=AAA1"), "#REF!")
        self.assertEqual(value("=1+"), "#PARSE!")
        self.assertEqual(value("=(1+2"), "#PARSE!")
        self.assertEqual(value("="), "#PARSE!")


class Graph(unittest.TestCase):
    def test_recalculates_after_changes(self):
        s = sheet(A1="1", A2="=A1*10", A3="=A2+A1")
        self.assertEqual(s.get("A3"), 11)
        s.set("A1", "2")
        self.assertEqual(s.get("A3"), 22)
        s.set("A2", "5")
        self.assertEqual(s.get("A3"), 7)
        s.set("A1", "")
        self.assertEqual(s.get("A3"), 5)

    def test_cycles_and_dependents(self):
        s = sheet(A1="=B1", B1="=A1+1", C1="=A1*2", D1="=5", E1="=E1")
        for cell in ("A1", "B1", "C1", "E1"):
            self.assertEqual(s.get(cell), "#CYCLE!", cell)
        self.assertEqual(s.get("D1"), 5)
        s.set("B1", "3")
        self.assertEqual(s.get("C1"), 6)

    def test_cycle_through_range(self):
        s = sheet(A1="=SUM(A2:A3)", A2="1", A3="=A1")
        self.assertEqual(s.get("A1"), "#CYCLE!")
        self.assertEqual(s.get("A2"), 1)

    def test_long_chain(self):
        s = Sheet()
        start = time.time()
        s.set("A1", "1")
        for i in range(2, 3001):
            s.set(f"A{i}", f"=A{i - 1}+1")
        self.assertEqual(s.get("A3000"), 3000)
        s.set("A1", "10")
        self.assertEqual(s.get("A3000"), 3009)
        self.assertLess(time.time() - start, 5)

    def test_absolute_refs(self):
        self.assertEqual(value("=$A$1+A$1+$A1", A1="2"), 6)


if __name__ == "__main__":
    unittest.main()
