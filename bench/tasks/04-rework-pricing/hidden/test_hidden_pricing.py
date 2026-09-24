import unittest
from decimal import Decimal

from pricing import price_order


def item(price, qty, sku="x"):
    return {"sku": sku, "unit_price": price, "qty": qty}


def dec(result):
    return {k: str(v) for k, v in result.items()}


class Basics(unittest.TestCase):
    def test_returns_decimals_quantized(self):
        result = price_order([item(0.1, 3)], "OR")
        for key in ("subtotal", "discount", "tax", "shipping", "total"):
            self.assertIsInstance(result[key], Decimal, key)
            self.assertEqual(result[key].as_tuple().exponent, -2, key)
        self.assertEqual(dec(result), {"subtotal": "0.30", "discount": "0.00", "tax": "0.00", "shipping": "5.99", "total": "6.29"})

    def test_input_types(self):
        result = price_order([item("1.10", 1), item(2, 1), item(Decimal("3.30"), 1), item(4.4, 1)], "OR")
        self.assertEqual(result["subtotal"], Decimal("10.80"))

    def test_tax_rates_and_rounding(self):
        self.assertEqual(price_order([item("10.00", 1)], "CA")["tax"], Decimal("0.73"))
        self.assertEqual(price_order([item("10.00", 1)], "NY")["tax"], Decimal("0.89"))
        self.assertEqual(price_order([item("10.00", 1)], "TX")["tax"], Decimal("0.63"))

    def test_unknown_region(self):
        with self.assertRaises(ValueError):
            price_order([item("1", 1)], "ZZ")

    def test_empty_order(self):
        self.assertEqual(dec(price_order([], "CA")), {"subtotal": "0.00", "discount": "0.00", "tax": "0.00", "shipping": "0.00", "total": "0.00"})


class Validation(unittest.TestCase):
    def test_bad_qty(self):
        for qty in (0, -1, 1.5, True, "2"):
            with self.assertRaises(ValueError, msg=repr(qty)):
                price_order([item("1", qty)], "OR")

    def test_negative_price(self):
        with self.assertRaises(ValueError):
            price_order([item("-1", 1)], "OR")


class Bulk(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(price_order([item("1.00", 9)], "OR")["subtotal"], Decimal("9.00"))
        self.assertEqual(price_order([item("1.00", 10)], "OR")["subtotal"], Decimal("9.50"))
        self.assertEqual(price_order([item("1.00", 49)], "OR")["subtotal"], Decimal("46.55"))
        self.assertEqual(price_order([item("1.00", 50)], "OR")["subtotal"], Decimal("45.00"))

    def test_line_rounding_half_up(self):
        # 0.33 * 10 * 0.95 = 3.135 -> 3.14 per line
        result = price_order([item("0.33", 10, "a"), item("0.33", 10, "b")], "OR")
        self.assertEqual(result["subtotal"], Decimal("6.28"))


class Coupons(unittest.TestCase):
    def test_percent_then_fixed(self):
        result = price_order([item("100.00", 1)], "CA", [{"type": "fixed", "value": "5.00"}, {"type": "percent", "value": 10}])
        self.assertEqual(dec(result), {"subtotal": "100.00", "discount": "15.00", "tax": "6.16", "shipping": "0.00", "total": "91.16"})

    def test_percent_rounding(self):
        result = price_order([item("19.99", 1)], "OR", [{"type": "percent", "value": 15}])
        self.assertEqual(result["discount"], Decimal("3.00"))
        self.assertEqual(result["total"], Decimal("22.98"))

    def test_floor_at_zero(self):
        result = price_order([item("4.00", 1)], "NY", [{"type": "fixed", "value": 10}])
        self.assertEqual(dec(result), {"subtotal": "4.00", "discount": "4.00", "tax": "0.00", "shipping": "5.99", "total": "5.99"})

    def test_invalid_coupons(self):
        for coupons in ([{"type": "percent", "value": 5}, {"type": "percent", "value": 5}],
                        [{"type": "bogo", "value": 1}]):
            with self.assertRaises(ValueError):
                price_order([item("1", 1)], "OR", coupons)


class Shipping(unittest.TestCase):
    def test_threshold_after_discount(self):
        self.assertEqual(price_order([item("50.00", 1)], "OR")["shipping"], Decimal("0.00"))
        self.assertEqual(price_order([item("49.99", 1)], "OR")["shipping"], Decimal("5.99"))
        result = price_order([item("55.00", 1)], "OR", [{"type": "fixed", "value": "5.01"}])
        self.assertEqual(result["shipping"], Decimal("5.99"))

    def test_total_formula(self):
        result = price_order([item("12.345", 3), item("7.10", 12)], "NY", [{"type": "percent", "value": 7.5}])
        self.assertEqual(result["total"], result["subtotal"] - result["discount"] + result["tax"] + result["shipping"])
        self.assertEqual(dec(result), {"subtotal": "117.98", "discount": "8.85", "tax": "9.69", "shipping": "0.00", "total": "118.82"})


if __name__ == "__main__":
    unittest.main()
