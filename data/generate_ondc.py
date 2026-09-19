"""
ShelfSense — step 2 fixture: ONDC orders.

The gate test (engine/disambiguate.py) returned FALLBACK: guessing the basket from a
Soundbox amount is right only ~31% of the time. The fallback plan makes ONDC orders
the inventory truth, because an ONDC order arrives ITEMISED — the shop knows exactly
which SKUs and how many left the shelf.

This script adds that channel to the synthetic world:
  ondc_orders.json   30 days of itemised online orders, same dates as transactions.json

It uses its own random seed and does NOT touch the existing fixtures, so the gate
test result stays reproducible.
"""

import json, random
from datetime import datetime, timedelta
from pathlib import Path

RNG = random.Random(20260918)
OUT = Path(__file__).resolve().parent

ORDERS_PER_DAY = 11            # a small kirana just starting on ONDC
LINES_PER_ORDER = [(1, 0.20), (2, 0.30), (3, 0.25), (4, 0.15), (5, 0.10)]
ORDER_HOURS = {9: 1.0, 10: 1.3, 11: 1.2, 12: 0.8, 13: 0.7, 14: 0.6, 15: 0.6,
               16: 0.8, 17: 1.1, 18: 1.4, 19: 1.5, 20: 1.2, 21: 0.7}
# Online baskets lean toward planned, heavier staples; impulse items less so.
CATEGORY_TILT = {"Staples": 2.0, "Oil": 2.0, "Dairy": 1.4, "Home Care": 1.5,
                 "Spices": 1.5, "Spreads": 1.3, "Beverages": 1.0, "Personal": 1.1,
                 "Noodles": 1.0, "Biscuits": 0.8, "Snacks": 0.7, "Confection": 0.4}


def pick(weighted):
    r, acc = RNG.random(), 0.0
    for v, p in weighted:
        acc += p
        if r <= acc:
            return v
    return weighted[-1][0]


def main(days=30):
    catalogue = json.loads((OUT / "catalogue.json").read_text())
    weights = [c["base_daily_units"] * CATEGORY_TILT.get(c["category"], 1.0)
               for c in catalogue]
    price = {c["sku_id"]: c["selling_price"] for c in catalogue}
    hours, hw = list(ORDER_HOURS), list(ORDER_HOURS.values())

    start = datetime(2026, 9, 17) - timedelta(days=days)   # same window as transactions
    orders, oid = [], 1
    for d in range(days):
        day = start + timedelta(days=d)
        mult = 1.25 if day.weekday() >= 5 else 1.0
        if 12 <= d <= 16:
            mult *= 1.18                                     # same festival week
        n = max(0, int(RNG.gauss(ORDERS_PER_DAY * mult, 2.5)))
        for _ in range(n):
            lines = {}
            for it in RNG.choices(catalogue, weights=weights, k=pick(LINES_PER_ORDER)):
                qty = 1 if RNG.random() < 0.75 else RNG.randint(2, 3)
                lines[it["sku_id"]] = lines.get(it["sku_id"], 0) + qty
            hour = RNG.choices(hours, weights=hw)[0]
            ts = day.replace(hour=hour, minute=RNG.randint(0, 59), second=RNG.randint(0, 59))
            orders.append({
                "order_id": f"ONDC{oid:05d}",
                "timestamp": ts.isoformat(),
                "buyer_app": RNG.choice(["Paytm", "Magicpin", "Ola", "PhonePe Pincode"]),
                "items": [{"sku_id": s, "qty": q} for s, q in sorted(lines.items())],
                "order_value": float(sum(price[s] * q for s, q in lines.items())),
                "status": "DELIVERED",
            })
            oid += 1

    orders.sort(key=lambda o: o["timestamp"])
    (OUT / "ondc_orders.json").write_text(json.dumps(orders, indent=2))
    units = sum(i["qty"] for o in orders for i in o["items"])
    print(f"ondc orders : {len(orders)} over {days} days, {units} units, "
          f"Rs {sum(o['order_value'] for o in orders):,.0f}")


if __name__ == "__main__":
    main()
