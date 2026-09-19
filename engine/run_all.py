"""
ShelfSense — run the whole pipeline in order.

    python3 engine/run_all.py            everything, using the practice data
    python3 engine/run_all.py --quick    skip the slow tests

Order:
  1. gate test          engine/disambiguate.py      can a payment amount tell us what sold?
  2. sales tracker      engine/ledger.py            units sold per product per day
  3. reorder list       engine/reorder.py           what to buy this morning, from whom
  4. phone screen       engine/build_screen.py      out/shopkeeper_screen.html
  5. deliveries+stock   engine/bills.py, stock.py   bills in, stock updated, next list
  6. expiry reminders   engine/expiry.py            batches that won't sell before they expire
  7. deployable app     engine/build_app.py         app/ — upload to any web host (see app/DEPLOY.md)

Practice data is regenerated only if it is missing (data/generate*.py).
"""

import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
QUICK = "--quick" in sys.argv


def run(*args, quiet=False, report=None):
    print(f"\n$ python3 {' '.join(args)}")
    r = subprocess.run([PY, *args], cwd=ROOT, capture_output=quiet, text=True)
    if report and r.returncode == 0:
        (ROOT / "out").mkdir(exist_ok=True)
        (ROOT / "out" / report).write_text(r.stdout)
    if r.returncode:
        if quiet:
            print(r.stdout[-2000:], r.stderr[-2000:])
        sys.exit(f"step failed: {args[0]}")
    if quiet:
        print("  done" + (f" — {r.stdout.strip().splitlines()[-1]}" if r.stdout.strip() else ""))


def need(path, *gen):
    if not (ROOT / path).exists():
        run(*gen)


need("data/transactions.json", "data/generate.py")
need("data/ondc_orders.json", "data/generate_ondc.py")
need("data/stock_count.json", "data/generate_stock_count.py")
need("data/barcodes.json", "data/generate_barcodes.py")

if not QUICK:
    run("engine/disambiguate.py", quiet=True, report="gate_report.txt")

# a shop with a billing station: bring in its export (skipped if already imported)
exports = sorted((ROOT / "data" / "pos").glob("*.csv")) if (ROOT / "data" / "pos").exists() else []
if not exports:
    run("data/generate_pos_sample.py")
    exports = sorted((ROOT / "data" / "pos").glob("*.csv"))
run("engine/pos_connector.py", "import", *[str(p.relative_to(ROOT)) for p in exports], quiet=True)

run("engine/ledger.py", quiet=True, report="ledger_report.txt")
need("data/stock_lots.json", "data/generate_expiry.py")
run("engine/reorder.py", *(["--no-test"] if QUICK else []), quiet=True, report="reorder_report.txt")
need("data/open_food_facts/india_products.json", "data/build_off_products.py")
run("engine/build_screen.py")
run("engine/build_app.py")
need("demo/demo_opening_stock.csv", "data/generate_demo_files.py")

# day 2: bills arrive, get checked and confirmed, stock moves, next list
need("data/bills/gupta_18-09-2026.txt", "data/generate_bills.py")
bills = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "data" / "bills").glob("*.txt"))
run("engine/bills.py", "read", *bills)
run("engine/bills.py", "confirm", "INV/GT/4471", "MW-26-09-0913", "SD/2026/1188")
run("engine/stock.py", quiet=True)
run("engine/expiry.py", quiet=True)
print("\nNext morning's list (from the updated stock):")
run("engine/reorder.py", "--stock", "out/stock_now.json", "--no-test", quiet=True,
    report="reorder_next_day_report.txt")
print(open(ROOT / "out" / "reorder_next_day_report.txt").read())
print("All done. In out/: gate_report.txt, ledger_report.txt, reorder_report.txt (first morning),"
      " stock_report.txt, expiry_report.txt, reorder_next_day_report.txt, shopkeeper_screen.html.\n"
      "reorder_today.json / purchase_orders.json always hold the most recent list; "
      "every day's list is kept in out/orders/.")
