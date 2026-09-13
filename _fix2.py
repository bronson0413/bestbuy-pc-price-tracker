import pathlib
p = pathlib.Path("src/tracker/sources/manual.py")
s = p.read_text(encoding="utf-8")
method = """    def history(self, sku):
        rows = sorted(
            (r for r in self.load() if r.get("sku") == sku),
            key=lambda r: r.get("captured_at_utc", ""),
        )
        quotes = []
        for row in rows:
            try:
                price = float(row["price_usd"])
            except (KeyError, ValueError):
                continue
            quotes.append(Quote(
                sku=sku, price_usd=price, source_method=self.name,
                availability=row.get("availability"),
                raw={"entered_by": row.get("entered_by", "unknown"),
                     "screenshot": row.get("screenshot", ""),
                     "captured_at_utc": row.get("captured_at_utc", "")}))
        return quotes

"""
s = s.replace("    def fetch(self, sku", method + "    def fetch(self, sku")
p.write_text(s, encoding="utf-8")
print("patched")
