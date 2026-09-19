"""
ShelfSense — expiry reminders for stock that won't sell in time.

For every product it walks through the batches on the shelf, earliest expiry
first, and asks: at the usual daily sales, how many of these pieces will still
be here on the expiry date? Anything left over is money about to be lost.

USAGE
    python3 engine/expiry.py            -> out/expiry_alerts.json, out/expiry_report.txt
                                           (reads out/stock_now.json from engine/stock.py)

WHAT THE SHOPKEEPER IS TOLD
    expired           remove it from the shelf now
    1-2 days left     sell today at a discount, or remove it
    3+ days left      put it at the front / offer a deal; ask the supplier about a return
"""

import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shelf import days_between  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / "data", ROOT / "out"
HORIZON_DAYS = 30        # only warn about batches expiring within this window


def alerts(lots_by_sku, today, rates, cost=None, horizon=HORIZON_DAYS):
    """lots_by_sku: {sku: [{qty, exp, est}]}; rates: pieces sold per day."""
    cost = cost or {}
    out = []
    for sku, lots in lots_by_sku.items():
        rate = max(0.0, rates.get(sku, 0.0))
        before = 0.0                           # pieces in batches that expire earlier
        for lot in sorted(lots, key=lambda l: l["exp"]):
            if lot["qty"] <= 0:
                continue
            left_days = days_between(today, lot["exp"])     # 0 = expires today
            if left_days > horizon:
                break
            if left_days < 0:
                leftover = lot["qty"]
            else:
                can_sell = max(0.0, rate * (left_days + 1) - before)   # includes today
                leftover = max(0.0, lot["qty"] - can_sell)
            before += lot["qty"]
            if leftover < 0.5:
                continue
            action = ("remove" if left_days < 0 else
                      "discount_or_remove" if left_days <= 2 else "push_or_return")
            out.append({
                "sku_id": sku, "exp": lot["exp"], "days_left": left_days,
                "batch_qty": lot["qty"], "unsold_qty": round(leftover),
                "value_at_risk": round(leftover * cost.get(sku, 0.0), 2),
                "expiry_estimated": bool(lot.get("est")), "action": action,
            })
    out.sort(key=lambda a: (a["days_left"], -a["value_at_risk"]))
    return out


SAY = {
    "remove": "expired {ago} — take it off the shelf",
    "discount_or_remove": "expires {when}; ~{n} won't sell — discount today or remove",
    "push_or_return": "expires {when}; ~{n} won't sell in time — put it in front, offer a deal, or ask the supplier about a return",
}


def when(d):
    return "today" if d == 0 else "tomorrow" if d == 1 else f"in {d} days"


def main():
    stock = json.loads((OUT / "stock_now.json").read_text())
    catalogue = {c["sku_id"]: c for c in json.loads((DATA / "catalogue.json").read_text())}
    ledger = {r["sku_id"]: r["est_daily_rate_recent"] for r in json.loads((OUT / "ledger.json").read_text())}
    sup = json.loads((DATA / "suppliers.json").read_text())
    cost = {}
    for c in sup["price_cards"]:
        cost[c["sku_id"]] = min(cost.get(c["sku_id"], 9e9), c["unit_cost"])
    today = stock["count_date"]
    found = alerts(stock.get("lots", {}), today, ledger, cost)
    removed = stock.get("expired_removed", [])

    lines = [f"EXPIRY CHECK FOR {today}", ""]
    if removed:
        lines.append("Already expired (not counted as stock any more):")
        for r in removed:
            c = catalogue[r["sku_id"]]
            lines.append(f"  {c['brand']} {c['product_name']} {c['pack_size']}: {r['qty']:g} pcs, "
                         f"expired {r['exp']} — remove from the shelf")
        lines.append("")
    if not found:
        lines.append("Nothing else at risk in the next 30 days.")
    for a in found:
        c = catalogue[a["sku_id"]]
        d = a["days_left"]
        text = SAY[a["action"]].format(ago=f"{-d} day(s) ago", when=when(d), n=a["unsold_qty"])
        est = " (date estimated)" if a["expiry_estimated"] else ""
        lines.append(f"  {c['brand']} {c['product_name']} {c['pack_size']}: {round(a['batch_qty'])} pcs, "
                     f"{text}. At risk Rs {a['value_at_risk']:,.0f}{est}")
    total = sum(a["value_at_risk"] for a in found)
    lines += ["", f"{len(found)} batch(es), Rs {total:,.0f} of stock at risk."]
    OUT.mkdir(exist_ok=True)
    (OUT / "expiry_alerts.json").write_text(json.dumps({"date": today, "alerts": found,
                                                        "expired_removed": removed}, indent=1))
    (OUT / "expiry_report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
