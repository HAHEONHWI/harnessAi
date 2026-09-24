import unittest
from decimal import Decimal

from pricing import price_order


class PricingTests(unittest.TestCase):
    def test_simple_order(self):
        result = price_order([{"sku": "a", "unit_price": "10.00", "qty": 2}], "OR")
        self.assertEqual(result["subtotal"], Decimal("20.00"))
        self.assertEqual(result["total"], Decimal("25.99"))


if __name__ == "__main__":
    unittest.main()
