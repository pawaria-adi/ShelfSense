"""
ShelfSense — shelf lives and stock batches, shared by the other programs.

A "lot" (batch) is some pieces of one product that expire on the same day:
    {"qty": 12, "exp": "2026-09-30", "est": false}
est = true means the expiry date is a guess from data/shelf_life.json rather
than read from a bill or entered by the shopkeeper.

Sales always use up the batch that expires first (first-expiry, first-out).
"""

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
LONG_LIFE = 60          # beyond this, shelf life never limits how much to stock


@lru_cache(maxsize=1)
def _table():
    t = json.loads((DATA / "shelf_life.json").read_text())
    cats = {c["sku_id"]: c["category"] for c in json.loads((DATA / "catalogue.json").read_text())}
    return t, cats


def shelf_days(sku):
    t, cats = _table()
    return int(t["product_days"].get(sku) or t["category_days"].get(cats.get(sku), 365))


def stocking_limit(sku):
    """Days of stock a perishable may hold before it spoils; None for long-life items."""
    d = shelf_days(sku)
    return d if d <= LONG_LIFE else None


def add_days(iso, n):
    return (date.fromisoformat(iso) + timedelta(days=int(round(n)))).isoformat()


def days_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def lots_total(lots):
    return sum(l["qty"] for l in lots)


def consume(lots, qty):
    """Take qty pieces, earliest expiry first. Returns pieces actually taken."""
    taken = 0.0
    for l in sorted(lots, key=lambda l: l["exp"]):
        if qty <= 0:
            break
        t = min(l["qty"], qty)
        l["qty"] -= t
        qty -= t
        taken += t
    lots[:] = [l for l in lots if l["qty"] > 1e-9]
    return taken


def drop_expired(lots, today):
    """Remove batches that expired before `today`. Returns the removed batches."""
    gone = [l for l in lots if l["exp"] < today]
    lots[:] = [l for l in lots if l["exp"] >= today]
    return gone
