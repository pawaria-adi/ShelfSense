"""
ShelfSense — step 5 fixture: a billing-station export.

Some shops bill every sale on a counter PC or tablet with a barcode scanner
(billing software such as Vyapar, Marg, GoFrugal or a Tally add-on). Those
programs can export each day's bills as a spreadsheet. This script fakes 30 days
of such an export for our shop, as if it had a scanner at the counter.

Realism: every sale is billed (cash too), but about 3% of items are keyed in as
"LOOSE ITEM" with no barcode (a scanner miss, or a product sold loose).

Writes data/pos/billing_export_2026-08-18_to_2026-09-16.csv
"""

import csv, json, random
from pathlib import Path

DATA = Path(__file__).resolve().parent
RNG = random.Random(20260921)
MISS_RATE = 0.03


def main():
    catalogue = {c["sku_id"]: c for c in json.loads((DATA / "catalogue.json").read_text())}
    ean_of = {sku: code for code, sku in json.loads((DATA / "barcodes.json").read_text()).items()}
    txns = json.loads((DATA / "transactions.json").read_text())
    (DATA / "pos").mkdir(exist_ok=True)
    first, last = txns[0]["timestamp"][:10], txns[-1]["timestamp"][:10]
    path = DATA / "pos" / f"billing_export_{first}_to_{last}.csv"
    rows = 0
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Bill Date", "Bill Time", "Bill No", "Item Code", "Item Name", "Qty", "Rate", "Amount"])
        for n, t in enumerate(txns, 1):
            d, tm = t["timestamp"][:10], t["timestamp"][11:19]
            day, mon, yr = d[8:10], d[5:7], d[:4]
            for sku, q in t["truth_basket"].items():
                c = catalogue[sku]
                name = f"{c['brand']} {c['product_name']} {c['pack_size']}"
                code = ean_of[sku]
                if RNG.random() < MISS_RATE:
                    code, name = "", "LOOSE ITEM"
                w.writerow([f"{day}/{mon}/{yr}", tm, f"B{n:05d}", code, name, q,
                            f"{c['selling_price']:.2f}", f"{c['selling_price'] * q:.2f}"])
                rows += 1
    print(f"billing export: {rows} item lines from {len(txns)} bills -> {path.name}")


if __name__ == "__main__":
    main()
