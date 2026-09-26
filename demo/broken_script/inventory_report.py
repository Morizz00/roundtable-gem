"""Inventory report: per-warehouse stock totals from a CSV export.

Usage: python inventory_report.py <stock.csv>
"""
import csv
import sys


def load_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def summarize(rows):
    warehouses = {}
    for row in rows:
        wh = row["warehouse"]
        qty = int(row["quantity"])
        price = float(row["unit_price"])
        entry = warehouses.setdefault(wh, {"skus": [], "units": 0, "value": 0.0})
        entry["skus"].append(row["sku"])
        entry["units"] += qty
        entry["value"] += qty * price
    return warehouses


def average_stock(entry):
    return entry["units"] / len(entry["skus"])


def main(argv):
    rows = load_rows(argv[1])
    for wh, entry in summarize(rows).items():
        print(f"{wh} units={entry['units']} value={entry['value']:.2f} avg={average_stock(entry):.2f}")


if __name__ == "__main__":
    main(sys.argv)
