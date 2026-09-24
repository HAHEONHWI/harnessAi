"""Small in-memory inventory used by the shop back office."""


class Inventory:
    def __init__(self):
        self._items = {}  # sku -> {"name": str, "qty": int, "price": float}

    def add(self, sku, name, qty, price):
        """Add stock. A new SKU needs a name and price; an existing SKU must keep its name."""
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

    def remove(self, sku, qty):
        item = self._items.get(sku)
        if item is None:
            raise KeyError(sku)
        if qty < 0 or qty > item["qty"]:
            raise ValueError("not enough stock")
        item["qty"] -= qty

    def quantity(self, sku):
        item = self._items.get(sku)
        return item["qty"] if item else 0

    def skus(self):
        return sorted(self._items)

    def total_value(self):
        return round(sum(item["price"] for item in self._items.values()), 2)
