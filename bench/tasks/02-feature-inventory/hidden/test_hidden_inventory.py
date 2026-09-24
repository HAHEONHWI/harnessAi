import unittest

from inventory import Inventory


def stocked():
    inv = Inventory()
    inv.add("B2", "Banana", 3, 0.5)
    inv.add("A1", "Apple", 3, 1.25)
    inv.add("C3", "Cherry", 1, 4.0)
    inv.add("D4", "Date", 10, 2.0)
    return inv


class LowStock(unittest.TestCase):
    def test_sorted_by_qty_then_sku(self):
        self.assertEqual(stocked().low_stock(5), ["C3", "A1", "B2"])

    def test_strictly_below(self):
        self.assertEqual(stocked().low_stock(3), ["C3"])
        self.assertEqual(stocked().low_stock(1), [])


class TotalValue(unittest.TestCase):
    def test_uses_quantity(self):
        self.assertEqual(stocked().total_value(), 29.25)

    def test_rounding(self):
        inv = Inventory()
        inv.add("X", "X", 3, 0.1)
        self.assertEqual(inv.total_value(), 0.3)


class ImportCsv(unittest.TestCase):
    def test_valid_rows(self):
        inv = Inventory()
        errors = inv.import_csv("sku,name,qty,price\nA1, Apple ,4,1.5\nB2,Banana,2,0.25\n")
        self.assertEqual(errors, [])
        self.assertEqual(inv.quantity("A1"), 4)
        self.assertEqual(inv.total_value(), 6.5)

    def test_error_line_numbers_and_skip(self):
        inv = Inventory()
        text = "sku,name,qty,price\nA1,Apple,1,1\nB2,Banana,x,1\n\nC3,Cherry,-1,1\nD4,Date,1,abc\nE5,,1,1\nF6,Fig,2\nG7,Grape,2,-3\n"
        errors = inv.import_csv(text)
        self.assertEqual([e.split(":")[0] for e in errors], ["line 3", "line 5", "line 6", "line 7", "line 8", "line 9"])
        self.assertEqual(inv.skus(), ["A1"])

    def test_name_conflict(self):
        inv = Inventory()
        inv.add("A1", "Apple", 1, 1)
        errors = inv.import_csv("sku,name,qty,price\nA1,Apricot,1,1\nA1,Apple,2,1\n")
        self.assertEqual([e.split(":")[0] for e in errors], ["line 2"])
        self.assertEqual(inv.quantity("A1"), 3)

    def test_existing_sku_adds_quantity(self):
        inv = stocked()
        self.assertEqual(inv.import_csv("sku,name,qty,price\nD4,Date,5,2\n"), [])
        self.assertEqual(inv.quantity("D4"), 15)


class Undo(unittest.TestCase):
    def test_nothing_to_undo(self):
        self.assertFalse(Inventory().undo())

    def test_undo_add_and_remove_in_order(self):
        inv = Inventory()
        inv.add("A1", "Apple", 5, 1)
        inv.remove("A1", 2)
        inv.add("A1", "Apple", 1, 1)
        self.assertTrue(inv.undo())
        self.assertEqual(inv.quantity("A1"), 3)
        self.assertTrue(inv.undo())
        self.assertEqual(inv.quantity("A1"), 5)
        self.assertTrue(inv.undo())
        self.assertEqual(inv.skus(), [])
        self.assertFalse(inv.undo())

    def test_failed_operations_not_recorded(self):
        inv = Inventory()
        inv.add("A1", "Apple", 1, 1)
        with self.assertRaises(ValueError):
            inv.remove("A1", 5)
        with self.assertRaises(ValueError):
            inv.add("A1", "Other", 1, 1)
        self.assertTrue(inv.undo())
        self.assertFalse(inv.undo())

    def test_import_undone_as_whole(self):
        inv = Inventory()
        inv.add("A1", "Apple", 1, 1)
        inv.import_csv("sku,name,qty,price\nB2,Banana,1,1\nC3,Cherry,1,1\nA1,Apple,4,1\n")
        self.assertTrue(inv.undo())
        self.assertEqual(inv.skus(), ["A1"])
        self.assertEqual(inv.quantity("A1"), 1)

    def test_empty_import_not_recorded(self):
        inv = Inventory()
        inv.add("A1", "Apple", 1, 1)
        inv.import_csv("sku,name,qty,price\nB2,Banana,bad,1\n")
        self.assertTrue(inv.undo())
        self.assertEqual(inv.skus(), [])


class Json(unittest.TestCase):
    def test_round_trip(self):
        inv = stocked()
        copy = Inventory.from_json(inv.to_json())
        self.assertIsInstance(copy, Inventory)
        self.assertEqual(copy.skus(), inv.skus())
        self.assertEqual([copy.quantity(s) for s in copy.skus()], [inv.quantity(s) for s in inv.skus()])
        self.assertEqual(copy.total_value(), inv.total_value())

    def test_loaded_inventory_is_usable_and_has_no_history(self):
        inv = Inventory.from_json(stocked().to_json())
        self.assertFalse(inv.undo())
        inv.remove("D4", 4)
        self.assertEqual(inv.quantity("D4"), 6)
        with self.assertRaises(ValueError):
            inv.add("A1", "Avocado", 1, 1)


if __name__ == "__main__":
    unittest.main()
