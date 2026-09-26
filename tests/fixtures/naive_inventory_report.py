"""The 'make the crash go away' fix a hurried model would write: guards the two crashes and nothing else."""
import csv
import sys


def load_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def summarize(rows):
    warehouses = {}
    for row in rows:
        try:
            qty = int(row["quantity"])
            price = float(row["unit_price"])
        except ValueError:
            continue
        wh = row["warehouse"]
        entry = warehouses.setdefault(wh, {"skus": [], "units": 0, "value": 0.0})
        entry["skus"].append(row["sku"])
        entry["units"] += qty
        entry["value"] += qty * price
    return warehouses


def average_stock(entry):
    if not entry["skus"]:
        return 0
    return entry["units"] / len(entry["skus"])


def main(argv):
    rows = load_rows(argv[1])
    for wh, entry in summarize(rows).items():
        print(f"{wh} units={entry['units']} value={entry['value']:.2f} avg={average_stock(entry):.2f}")


if __name__ == "__main__":
    main(sys.argv)
