"""
ShelfSense — build the shopkeeper's phone screen.

Bakes the latest results into one self-contained web page:
    out/shopkeeper_screen.html   open it in any browser (phone-sized layout)

Tabs:
  Order            today's list per supplier, edit, send on WhatsApp
  Deliveries       photo of the bill -> checked against the order -> stock updated
  Stock            what's on the shelf, recount, save a file for the computer
  Billing station  scan at the counter, import a billing-software export, live link

Hindi product names live in data/names_hi.json (name without pack size; the
pack size is added here in Hindi units). Every product must have one.

Run after engine/reorder.py:
    python3 engine/build_screen.py
"""

import base64, hashlib, json, re, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shelf import shelf_days  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT, ENGINE = ROOT / "data", ROOT / "out", ROOT / "engine"
OFFP = DATA / "open_food_facts" / "india_products.json"


HINDI_UNITS = {"g": "ग्राम", "kg": "किलो", "ml": "मि.ली.", "l": "लीटर"}


def hindi_pack(pack):
    m = re.fullmatch(r"([\d.]+)\s*([a-zA-Z]+)", pack.strip())
    if not m or m.group(2).lower() not in HINDI_UNITS:
        return pack
    return f"{m.group(1)} {HINDI_UNITS[m.group(2).lower()]}"


# The one account that opens the example shop (practice data). Only a salted hash of the
# PIN goes into the page. A 4-digit PIN can be guessed by someone who reads the page code,
# so this account must never hold real shop data.
ADMIN_MOBILE, ADMIN_PIN = "1234567890", "3456"
EXAMPLE_SHOP_NAME = "Demo Kirana Store"


def admin_record():
    salt = hashlib.sha256(b"shelfsense-example-admin").digest()[:16]   # fixed: rebuilds give the same page
    digest = hashlib.pbkdf2_hmac("sha256", ADMIN_PIN.encode(), salt, 150000, 32)
    return {"mobile": ADMIN_MOBILE, "salt": base64.b64encode(salt).decode(),
            "hash": base64.b64encode(digest).decode(), "len": len(ADMIN_PIN)}


def build():
    sup = json.loads((DATA / "suppliers.json").read_text())
    cards = {}
    for c in sup["price_cards"]:
        cards.setdefault(c["supplier_id"], {})[c["sku_id"]] = c["unit_cost"]
    card_rows = {(c["supplier_id"], c["sku_id"]): c for c in sup["price_cards"]}
    by_name = {s["name"]: s for s in sup["suppliers"]}
    catalogue = json.loads((DATA / "catalogue.json").read_text())
    names_hi = json.loads((DATA / "names_hi.json").read_text())
    missing = [c["sku_id"] for c in catalogue if not names_hi.get(c["sku_id"])]
    if missing:
        raise SystemExit(f"data/names_hi.json has no Hindi name for: {', '.join(missing)}")
    ean_of = {sku: code for code, sku in json.loads((DATA / "barcodes.json").read_text()).items()}
    count = json.loads((DATA / "stock_count.json").read_text())
    rpath = OUT / "reorder_result.json"          # absent after a --quick run
    result = json.loads(rpath.read_text()) if rpath.exists() else None
    order_date = count["count_date"]
    order = json.loads((OUT / "orders" / f"orders_{order_date}.json").read_text())["lines"]
    ledger = json.loads((OUT / "ledger.json").read_text())

    lines = []
    for r in order:
        s = by_name[r["buy_from"]]
        card = card_rows[(s["supplier_id"], r["sku_id"])]
        min_packs = s["moq_cartons"] if r["pack_size"] == card["units_per_carton"] else 1
        lines.append({
            "sku_id": r["sku_id"], "on_hand": r["on_hand"], "est_daily_rate": r["est_daily_rate"],
            "days_left": r["days_left"] or 0.0, "supplier_id": s["supplier_id"],
            "packs": r["packs"], "pack_size": r["pack_size"], "min_packs": min_packs,
            "unit_cost": card["unit_cost"], "effective_unit_cost": r["effective_unit_cost"],
            "why": r["why"], "alternatives": r["alternatives"],
        })
    lines.sort(key=lambda l: l["days_left"])

    sample_bill = None
    png = DATA / "bills" / "metro_18-09-2026.png"
    if png.exists():
        sample_bill = {"supplier_id": "SUP02",
                       "data_uri": "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()}

    lots_file = DATA / "stock_lots.json"
    lots = json.loads(lots_file.read_text()) if lots_file.exists() else {}
    lots = lots.get("lots", {}) if lots.get("count_date") == count["count_date"] else {}
    lots = {sku: [dict(l, lid=f"c-{sku}-{i}") for i, l in enumerate(ls)] for sku, ls in lots.items()}
    cost = {}
    for c in sup["price_cards"]:
        cost[c["sku_id"]] = min(cost.get(c["sku_id"], 9e9), c["unit_cost"])

    sim = result["simulation"] if result and result.get("date") == order_date else None
    d = date.fromisoformat(order_date)
    payload = {
        "date": order_date,
        "version": "v0.8 · " + order_date,
        "date_label": f"{d.strftime('%a')}, {d.day} {d.strftime('%b')}",
        "catalogue": [{"sku_id": c["sku_id"], "name": f"{c['brand']} {c['product_name']} {c['pack_size']}",
                       "name_hi": f"{names_hi[c['sku_id']]} {hindi_pack(c['pack_size'])}",
                       "category": c["category"], "mrp": c["mrp"], "price": c.get("selling_price", c["mrp"]), "ean": ean_of[c["sku_id"]]}
                      for c in catalogue],
        "suppliers": [{"id": s["supplier_id"], "name": s["name"], "upi": s["upi"],
                       "credit_days": s["credit_days"], "moq_cartons": s["moq_cartons"],
                       "lead_hours": s["lead_time_hours"]} for s in sup["suppliers"]],
        "cards": cards,
        "lines": lines,
        "stock": {"count_date": count["count_date"],
                  "counts": {c["sku_id"]: c["on_hand"] for c in count["counts"]},
                  "lots": lots},
        "shelf_days": {c["sku_id"]: shelf_days(c["sku_id"]) for c in catalogue},
        "cost": cost,
        "rates": {r["sku_id"]: r["est_daily_rate_recent"] for r in ledger},
        "sample_bill": sample_bill,
        "admin": admin_record(),
        # extra products a shop can add (Open Food Facts, ODbL) — see data/build_off_products.py
        "off_products": json.loads(OFFP.read_text()) if OFFP.exists() else [],
        "example_shop_name": EXAMPLE_SHOP_NAME,
        "sim": {"days": 14, "runs": 200,
                "shelfsense": sim["ShelfSense"], "pos": sim.get("+ billing station"),
                "gut": sim["Gut feel"], "oracle": sim["Perfect knowledge"]} if sim else None,
    }
    template = (ENGINE / "screen_template.html").read_text()
    return template.replace("/*__DATA__*/null", json.dumps(payload, ensure_ascii=False))


def main():
    body = build()
    page = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '<style>body{margin:0}[hidden]{display:none!important}</style>\n'
            '</head><body>\n' + body + '\n</body></html>\n')
    OUT.mkdir(exist_ok=True)
    (OUT / "shopkeeper_screen.html").write_text(page)
    (OUT / "shopkeeper_screen.fragment.html").write_text(body)   # for web publishing
    print(f"wrote out/shopkeeper_screen.html ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main()
