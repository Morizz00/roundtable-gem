import csv
import os
import sys


def main(argv):
    path = argv[1] if len(argv) > 1 else ""
    if not os.path.isfile(path):
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2
    warehouses = {}
    with open(path, newline="") as f:
        for lineno, row in enumerate(csv.DictReader(f), start=2):
            entry = warehouses.setdefault(row["warehouse"], {"skus": {}, "value": 0.0})
            try:
                qty = int(row["quantity"])
                price = float(row["unit_price"])
            except (ValueError, TypeError) as e:
                print(f"SKIPPED line {lineno}: non-numeric value ({e})", file=sys.stderr)
                continue
            if qty < 0:
                print(f"SKIPPED line {lineno}: negative quantity", file=sys.stderr)
                continue
            entry["skus"][row["sku"]] = entry["skus"].get(row["sku"], 0) + qty
            entry["value"] += qty * price
    for wh in sorted(warehouses):
        e = warehouses[wh]
        units = sum(e["skus"].values())
        avg = f"{units / len(e['skus']):.2f}" if e["skus"] else "n/a"
        print(f"{wh} units={units} value={e['value']:.2f} avg={avg}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
