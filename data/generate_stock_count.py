"""
ShelfSense — step 3 fixture: today's shelf count.

To decide what to reorder, the app must know what is on the shelf NOW. In a real
shop this comes from a one-time count when the shopkeeper signs up (then a quick
recount once a week). This script fakes that count for 2026-09-17 morning.

It reads the ground truth (fine for a fixture generator, never for the engine) so
the counts are realistic: some shelves nearly empty, some over-stocked.
"""

import json, random
from collections import defaultdict
from pathlib import Path

RNG = random.Random(20260919)
DATA = Path(__file__).resolve().parent
COUNT_DATE = "2026-09-17"


def true_recent_rates(days=7):
    txns = json.loads((DATA / "transactions.json").read_text())
    orders = json.loads((DATA / "ondc_orders.json").read_text())
    all_days = sorted({t["timestamp"][:10] for t in txns})
    recent = set(all_days[-days:])
    u = defaultdict(float)
    for t in txns:
        if t["timestamp"][:10] in recent:
            for s, q in t["truth_basket"].items():
                u[s] += q
    for o in orders:
        if o["timestamp"][:10] in recent:
            for it in o["items"]:
                u[it["sku_id"]] += it["qty"]
    return {s: v / days for s, v in u.items()}


def main():
    catalogue = json.loads((DATA / "catalogue.json").read_text())
    rates = true_recent_rates()
    counts = []
    for c in catalogue:
        rate = rates.get(c["sku_id"], 0.1)
        r = RNG.random()
        if r < 0.08:
            cover = 0.0                              # already out
        elif r < 0.35:
            cover = RNG.uniform(0.3, 2.5)            # running low
        else:
            cover = RNG.uniform(2.5, 14.0)           # fine / over-stocked
        if any(k in c["product_name"] for k in ("Milk", "Dahi")):
            cover = min(cover, RNG.uniform(0.3, 1.5))   # fresh items are never deep-stocked
        counts.append({"sku_id": c["sku_id"], "on_hand": int(round(rate * cover))})
    (DATA / "stock_count.json").write_text(json.dumps(
        {"count_date": COUNT_DATE, "counts": counts}, indent=2))
    print(f"shelf count {COUNT_DATE}: {len(counts)} SKUs, "
          f"{sum(1 for x in counts if x['on_hand'] == 0)} at zero")


if __name__ == "__main__":
    main()
