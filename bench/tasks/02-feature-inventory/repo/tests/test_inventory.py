import unittest

from inventory import Inventory


class InventoryTests(unittest.TestCase):
    def test_add_and_remove(self):
        inv = Inventory()
        inv.add("A1", "Apple", 5, 1.5)
        inv.remove("A1", 2)
        self.assertEqual(inv.quantity("A1"), 3)

    def test_remove_too_much(self):
        inv = Inventory()
        inv.add("A1", "Apple", 1, 1.0)
        with self.assertRaises(ValueError):
            inv.remove("A1", 2)


if __name__ == "__main__":
    unittest.main()
