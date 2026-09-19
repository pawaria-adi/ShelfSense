"""
ShelfSense — step 5 fixture: supplier bills for the 17 Sep orders.

When a delivery arrives, the distributor hands over a printed GST bill. These
fake bills let us test "upload the bill -> check it against the order -> update
stock". Each is written as text (.txt) and as a photo-like image (.png) so both
the Python reader and the phone screen's photo upload can be tried.

Deliberate problems, because real deliveries have them:
  Gupta Traders : Lay's short by one carton, Kurkure missing, Coca-Cola price
                  went up, an unordered carton of Parle-G, and Dove shampoo
                  replaced by Sunsilk (not in our catalogue).
  Metro         : Maggi 4 cartons instead of 5.
  Sharma        : exactly as ordered.

Reads out/reorder_today.json (the order) and data/suppliers.json (prices).
"""

import json
from pathlib import Path

DATA = Path(__file__).resolve().parent
OUT = DATA.parent / "out"
BILLS = DATA / "bills"
BILL_DATE = "18-09-2026"
SHOP = "Demo Kirana Store, Sector 4 Market"

SUP_INFO = {
    "SUP01": ("GUPTA TRADERS", "Shop 12, Grain Market", "INV/GT/4471", "DEMO-GSTIN-GT01"),
    "SUP02": ("METRO WHOLESALERS", "Plot 7, Industrial Area", "MW-26-09-0913", "DEMO-GSTIN-MW02"),
    "SUP03": ("SHARMA DISTRIBUTORS", "B-3, Station Road", "SD/2026/1188", "DEMO-GSTIN-SD03"),
}


def bill_name(item):
    """How a distributor's billing software prints the item: shouty and abbreviated."""
    name = f"{item['brand']} {item['product_name']} {item['pack_size']}".upper()
    for a, b in (("BISCUITS", "BISC"), ("DETERGENT", "DET"), ("POWDER", "PWD"), ("SHAMPOO", "SHMP"),
                 ("NOODLES", "NDL"), ("CHOCOLATE", "CHOC"), ("SOFT DRINK", "SD"), ("'", "")):
        name = name.replace(a, b)
    return name


def make_lines(sid, order, catalogue, cards):
    by_name = {f"{c['brand']} {c['product_name']} {c['pack_size']}": c for c in catalogue.values()}
    lines = []
    for r in order:
        item = by_name[r["name"]]
        cost = cards[(sid, item["sku_id"])]["unit_cost"]
        qty = r["qty"]
        name = bill_name(item)
        if sid == "SUP01":
            if item["product_name"] == "Masala Munch":
                continue                                   # not supplied
            if item["brand"] == "Lay's":
                qty -= 24                                  # short by a carton
            if item["brand"] == "Coca-Cola":
                cost = round(cost * 1.05, 2)               # price rise
            if item["brand"] == "Dove":
                name, item = "SUNSILK BLACK SHINE SHMP 180ML", None   # substitute
        if sid == "SUP02" and item and item["brand"] == "Maggi":
            qty -= 24
        lines.append({"name": name, "hsn": item["hsn_code"] if item else "3305",
                      "gst": item["gst_rate"] if item else 18, "qty": qty, "rate": cost})
    if sid == "SUP01":
        parle = next(c for c in catalogue.values() if c["product_name"] == "Parle-G Biscuits")
        lines.append({"name": bill_name(parle), "hsn": parle["hsn_code"], "gst": parle["gst_rate"],
                      "qty": 24, "rate": cards[(sid, parle["sku_id"])]["unit_cost"]})
    return lines


def as_text(sid, lines):
    name, addr, inv, gstin = SUP_INFO[sid]
    out = [name, addr, f"GSTIN: {gstin}", "TAX INVOICE", f"Invoice No: {inv}    Date: {BILL_DATE}",
           f"Bill To: {SHOP}", "-" * 78,
           f"{'#':>2}  {'ITEM':<34}{'HSN':>6}{'GST%':>6}{'QTY':>6}{'RATE':>10}{'AMOUNT':>12}", "-" * 78]
    total = 0.0
    for i, l in enumerate(lines, 1):
        amt = l["qty"] * l["rate"]
        total += amt
        out.append(f"{i:>2}  {l['name'][:34]:<34}{l['hsn']:>6}{l['gst']:>6}{l['qty']:>6}"
                   f"{l['rate']:>10.2f}{amt:>12.2f}")
    out += ["-" * 78, f"{'TOTAL (incl. GST)':>66}{total:>12.2f}", f"Items: {len(lines)}",
            "Goods once sold will not be taken back. E&OE."]
    return out, total


def as_images(sid, text_lines, per_page=30):
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("DejaVuSansMono.ttf", 17)
        bold = ImageFont.truetype("DejaVuSansMono-Bold.ttf", 22)
    except OSError:
        font = bold = ImageFont.load_default()
    head, body, foot = text_lines[:9], text_lines[9:-4], text_lines[-4:]
    pages = [body[i:i + per_page] for i in range(0, len(body), per_page)] or [[]]
    paths = []
    for p, chunk in enumerate(pages, 1):
        rows = head + chunk + (foot if p == len(pages) else ["-" * 78, f"continued... page {p}/{len(pages)}"])
        w, h = 900, 40 + 24 * len(rows) + 40
        img = Image.new("RGB", (w, h), (250, 249, 244))
        d = ImageDraw.Draw(img)
        y = 30
        for i, row in enumerate(rows):
            d.text((30, y), row, fill=(30, 30, 40), font=bold if i in (0, 3) else font)
            y += 24
        img = img.rotate(-0.6, expand=True, fillcolor=(236, 233, 224))   # a slightly crooked photo
        suffix = f"_p{p}" if len(pages) > 1 else ""
        path = BILLS / f"{SUP_INFO[sid][0].split()[0].lower()}_{BILL_DATE}{suffix}.png"
        img.save(path, optimize=True)
        paths.append(path.name)
    return paths


def main():
    catalogue = {c["sku_id"]: c for c in json.loads((DATA / "catalogue.json").read_text())}
    sup = json.loads((DATA / "suppliers.json").read_text())
    cards = {(c["supplier_id"], c["sku_id"]): c for c in sup["price_cards"]}
    names = {s["name"]: s["supplier_id"] for s in sup["suppliers"]}
    today = json.loads((OUT / "reorder_today.json").read_text())
    BILLS.mkdir(exist_ok=True)
    for sname, sid in names.items():
        order = [r for r in today if r["buy_from"] == sname]
        if not order:
            continue
        lines = make_lines(sid, order, catalogue, cards)
        text, total = as_text(sid, lines)
        base = f"{SUP_INFO[sid][0].split()[0].lower()}_{BILL_DATE}"
        (BILLS / f"{base}.txt").write_text("\n".join(text) + "\n")
        imgs = as_images(sid, text)
        print(f"{sname:<20} {len(lines):>3} lines  Rs {total:>10,.2f}  -> {base}.txt, {', '.join(imgs)}")


if __name__ == "__main__":
    main()
