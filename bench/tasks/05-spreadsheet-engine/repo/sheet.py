"""Spreadsheet engine for the budgeting app. Formulas are not supported yet."""


class Sheet:
    def __init__(self):
        self._raw = {}

    def set(self, cell, text):
        self._raw[cell.upper()] = text

    def formula(self, cell):
        return self._raw.get(cell.upper(), "")

    def get(self, cell):
        text = self._raw.get(cell.upper(), "")
        if text == "":
            return None
        try:
            return float(text)
        except ValueError:
            return text
