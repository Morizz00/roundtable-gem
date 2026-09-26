"""Hidden verification suite for demo/broken_script/inventory_report.py.

Only the CRITIC agent runs this (mounted into its own sandbox next to the coder's
inventory_report.py); the coder never sees it. Black-box on purpose: it runs the script
as a subprocess, so it does not depend on the coder's internal function names.
Stdlib only (unittest), so it needs nothing installed in the sandbox.

    python -m unittest test_hidden -v
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory_report.py")
HEADER = "warehouse,sku,quantity,unit_price\n"


def run_csv(rows: str):
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
        f.write(HEADER + rows)
        path = f.name
    try:
        return subprocess.run([sys.executable, SCRIPT, path], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(path)


def lines(text: str):
    return [ln for ln in text.splitlines() if ln.strip()]


class InventoryReportSpec(unittest.TestCase):
    def test_format_and_alphabetical_order(self):
        p = run_csv("Zeta,Z1,4,2.00\nAlpha,A1,10,1.50\nAlpha,A2,6,0.50\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(lines(p.stdout), ["Alpha units=16 value=18.00 avg=8.00", "Zeta units=4 value=8.00 avg=4.00"])

    def test_non_numeric_quantity_is_skipped_and_logged(self):
        p = run_csv("North,A1,5,2.00\nNorth,A2,abc,2.00\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(lines(p.stdout), ["North units=5 value=10.00 avg=5.00"])
        self.assertTrue(re.search(r"^SKIPPED line 3: .+", p.stderr, re.M), p.stderr)

    def test_non_numeric_price_is_skipped_and_logged(self):
        p = run_csv("North,A1,5,2.00\nNorth,A2,3,free\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(lines(p.stdout), ["North units=5 value=10.00 avg=5.00"])
        self.assertTrue(re.search(r"^SKIPPED line 3: .+", p.stderr, re.M), p.stderr)

    def test_negative_quantity_is_rejected(self):
        p = run_csv("North,A1,5,2.00\nNorth,A2,-4,2.00\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(lines(p.stdout), ["North units=5 value=10.00 avg=5.00"])
        self.assertTrue(re.search(r"^SKIPPED line 3: .+", p.stderr, re.M), p.stderr)

    def test_duplicate_skus_are_summed_and_averaged_per_distinct_sku(self):
        p = run_csv("North,A1,5,2.00\nNorth,A1,3,2.00\nNorth,A2,4,1.00\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(lines(p.stdout), ["North units=12 value=20.00 avg=6.00"])

    def test_warehouse_with_no_valid_rows_is_still_reported(self):
        p = run_csv("East,C1,abc,9.99\nWest,W1,2,1.00\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(lines(p.stdout), ["East units=0 value=0.00 avg=n/a", "West units=2 value=2.00 avg=2.00"])
        self.assertTrue(re.search(r"^SKIPPED line 2: .+", p.stderr, re.M), p.stderr)

    def test_missing_file_exits_2_without_a_traceback(self):
        p = subprocess.run([sys.executable, SCRIPT, os.path.join(tempfile.gettempdir(), "no_such_stock_file.csv")],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("error: file not found:", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_header_only_file_prints_nothing(self):
        p = run_csv("")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(lines(p.stdout), [])


if __name__ == "__main__":
    unittest.main()
