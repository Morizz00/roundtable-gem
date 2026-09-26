# inventory_report.py: expected behavior

`python inventory_report.py <stock.csv>` reads a CSV with the header
`warehouse,sku,quantity,unit_price` and prints one line per warehouse.
Line numbers below are 1-based file line numbers; the header is line 1.

1. **Output format.** One line per warehouse, exactly:
   `<warehouse> units=<int> value=<2 decimals> avg=<2 decimals or n/a>`
   - `units` = total valid quantity in that warehouse.
   - `value` = sum of `quantity * unit_price` over valid rows.
   - `avg` = `units` divided by the number of **distinct SKUs** with valid rows.
2. **Order.** Warehouses are printed sorted alphabetically.
3. **Non-numeric values.** A row whose `quantity` or `unit_price` is not a number is skipped and
   reported on stderr as `SKIPPED line <n>: <reason>`.
4. **Negative quantities.** A row with a negative `quantity` is skipped and reported the same way.
5. **Duplicate SKUs.** Several rows with the same warehouse and SKU are summed into one SKU entry.
6. **No valid rows.** A warehouse that appears in the file but has no valid rows is still printed,
   with `units=0 value=0.00 avg=n/a`.
7. **Exit codes.** Exit code 0 even when rows were skipped. If the file does not exist, print
   `error: file not found: <path>` to stderr and exit with code 2 (no traceback).
