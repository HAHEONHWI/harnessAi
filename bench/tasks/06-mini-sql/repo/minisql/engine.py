"""Only CREATE TABLE, INSERT ... VALUES and SELECT * FROM t work so far."""
import re


class SQLError(Exception):
    pass


class Database:
    def __init__(self):
        self.tables = {}  # name -> {"columns": [(name, type)], "rows": [list]}

    def execute(self, sql):
        sql = sql.strip().rstrip(";").strip()
        m = re.match(r"(?is)create\s+table\s+(\w+)\s*\((.*)\)$", sql)
        if m:
            cols = [c.split() for c in m.group(2).split(",")]
            self.tables[m.group(1).lower()] = {"columns": [(c[0].lower(), c[1].upper()) for c in cols], "rows": []}
            return None
        m = re.match(r"(?is)insert\s+into\s+(\w+)\s+values\s*\((.*)\)$", sql)
        if m:
            values = []
            for raw in m.group(2).split(","):
                raw = raw.strip()
                if raw.startswith("'"):
                    values.append(raw[1:-1])
                elif raw.upper() == "NULL":
                    values.append(None)
                else:
                    values.append(float(raw) if "." in raw else int(raw))
            self.tables[m.group(1).lower()]["rows"].append(values)
            return 1
        m = re.match(r"(?is)select\s+\*\s+from\s+(\w+)$", sql)
        if m:
            return [tuple(r) for r in self.tables[m.group(1).lower()]["rows"]]
        raise SQLError(f"unsupported statement: {sql}")
