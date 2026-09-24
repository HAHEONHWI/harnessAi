"""Small in-memory inventory used by the shop back office."""
import copy
import csv
import io
import json


class Inventory:
    def __init__(self):
        self._items = {}  # sku -> {"name": str, "qty": int, "price": float}
        self._history = []

    def _checkpoint(self):
        return copy.deepcopy(self._items)

    def _apply_add(self, sku, name, qty, price):
        if qty < 0 or price < 0:
            raise ValueError("qty and price must be non-negative")
        item = self._items.get(sku)
        if item is None:
            self._items[sku] = {"name": name, "qty": qty, "price": price}
            return
        if item["name"] != name:
            raise ValueError(f"{sku} already exists as {item['name']!r}")
        item["qty"] += qty
        item["price"] = price

    def add(self, sku, name, qty, price):
        """Add stock. A new SKU needs a name and price; an existing SKU must keep its name."""
        before = self._checkpoint()
        self._apply_add(sku, name, qty, price)
        self._history.append(before)

    def remove(self, sku, qty):
        item = self._items.get(sku)
        if item is None:
            raise KeyError(sku)
        if qty < 0 or qty > item["qty"]:
            raise ValueError("not enough stock")
        self._history.append(self._checkpoint())
        item["qty"] -= qty

    def quantity(self, sku):
        item = self._items.get(sku)
        return item["qty"] if item else 0

    def skus(self):
        return sorted(self._items)

    def total_value(self):
        return round(sum(item["qty"] * item["price"] for item in self._items.values()), 2)

    def low_stock(self, threshold):
        low = [(item["qty"], sku) for sku, item in self._items.items() if item["qty"] < threshold]
        return [sku for _, sku in sorted(low)]

    def import_csv(self, text):
        before = self._checkpoint()
        errors, applied = [], 0
        rows = list(csv.reader(io.StringIO(text)))
        for number, row in enumerate(rows[1:], start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            cells = [cell.strip() for cell in row]
            if len(cells) < 4 or not all(cells[:4]):
                errors.append(f"line {number}: missing field")
                continue
            sku, name, qty_text, price_text = cells[:4]
            try:
                qty = int(qty_text)
            except ValueError:
                qty = -1
            if qty < 0:
                errors.append(f"line {number}: invalid qty {qty_text!r}")
                continue
            try:
                price = float(price_text)
            except ValueError:
                price = -1
            if price < 0 or price != price:
                errors.append(f"line {number}: invalid price {price_text!r}")
                continue
            existing = self._items.get(sku)
            if existing and existing["name"] != name:
                errors.append(f"line {number}: {sku} already exists as {existing['name']!r}")
                continue
            self._apply_add(sku, name, qty, price)
            applied += 1
        if applied:
            self._history.append(before)
        return errors

    def undo(self):
        if not self._history:
            return False
        self._items = self._history.pop()
        return True

    def to_json(self):
        return json.dumps([{"sku": sku, **item} for sku, item in sorted(self._items.items())])

    @classmethod
    def from_json(cls, text):
        inv = cls()
        for row in json.loads(text):
            inv._items[row["sku"]] = {"name": row["name"], "qty": row["qty"], "price": row["price"]}
        return inv
