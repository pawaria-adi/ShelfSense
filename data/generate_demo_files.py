"""
ShelfSense — demo files for a brand-new shop account.

Writes to demo/:
  demo_opening_stock.csv   Stock tab -> Start shelf count -> "Import a stock list (CSV)"
  demo_sales_week.csv      Billing station -> "Choose CSV file" (7 days of counter sales)
  demo_barcodes.pdf        print it (or show it on another screen) and scan with the camera
  README.txt               the 3-minute demo, step by step

Run:  python3 data/generate_demo_files.py [--end 2026-09-17]
"""
import argparse, csv, json, random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / "data", ROOT / "demo"

# 20 fast-moving products from the catalogue, with a believable opening count
PICK = {"SKU014": 24, "SKU064": 120, "SKU010": 60, "SKU001": 72, "SKU015": 18, "SKU047": 30, "SKU006": 30,
        "SKU007": 24, "SKU002": 36, "SKU093": 40, "SKU072": 20, "SKU085": 30, "SKU041": 18, "SKU076": 15,
        "SKU086": 24, "SKU056": 20, "SKU088": 30, "SKU084": 20, "SKU027": 12, "SKU045": 12}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default="2026-09-17", help="last day of the sales file")
    args = ap.parse_args()
    end = date.fromisoformat(args.end)
    cat = {c["sku_id"]: c for c in json.loads((DATA / "catalogue.json").read_text())}
    ean = {sku: code for code, sku in json.loads((DATA / "barcodes.json").read_text()).items()}
    life = json.loads((DATA / "shelf_life.json").read_text())
    shelf_days = lambda sku: (life.get("product_days", {}).get(sku) or life.get("category_days", {}).get(cat[sku]["category"]) or 180)
    name = lambda c: f"{c['brand']} {c['product_name']} {c['pack_size']}".replace("Parle Parle-G", "Parle-G")
    OUT.mkdir(exist_ok=True)
    rnd = random.Random(7)

    # 1. opening stock: barcode, name, qty, expiry (a few expire soon, to show the reminder)
    with open(OUT / "demo_opening_stock.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Item Code", "Item Name", "Qty", "Expiry"])
        for i, (sku, qty) in enumerate(PICK.items()):
            left = 3 if i in (0, 9) else max(2, int(shelf_days(sku) * rnd.uniform(0.3, 0.8)))
            w.writerow([ean[sku], name(cat[sku]), qty, (end + timedelta(days=left)).strftime("%d/%m/%Y")])

    # 2. a week of counter sales in the billing-software export format
    with open(OUT / "demo_sales_week.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Bill Date", "Bill Time", "Bill No", "Item Code", "Item Name", "Qty", "Rate", "Amount"])
        bill = 0
        for back in range(6, -1, -1):
            d = end - timedelta(days=back)
            lines = []
            for sku in PICK:
                n = max(0, round(cat[sku]["base_daily_units"] * rnd.uniform(0.08, 0.16)))   # a small shop's share
                lines += [sku] * n
            rnd.shuffle(lines)
            t = 7 * 3600
            while lines:
                bill += 1
                t += rnd.randint(300, 1500)
                basket = {}
                for _ in range(rnd.choice([1, 1, 1, 2, 2, 3])):
                    if lines:
                        s = lines.pop()
                        basket[s] = basket.get(s, 0) + 1
                for s, q in basket.items():
                    price = cat[s]["selling_price"]
                    w.writerow([d.strftime("%d/%m/%Y"), f"{min(t, 22 * 3600) // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}",
                                f"D{bill:05d}", ean[s], name(cat[s]), q, f"{price:.2f}", f"{price * q:.2f}"])

    # 3. printable barcodes
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.graphics.barcode import eanbc
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF
    pdf = canvas.Canvas(str(OUT / "demo_barcodes.pdf"), pagesize=A4)
    pdf.setTitle("ShelfSense demo barcodes")
    W, H = A4
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(15 * mm, H - 15 * mm, "ShelfSense demo barcodes — scan with Stock > Start shelf count > Camera")
    cols, cw, ch = 3, (W - 30 * mm) / 3, 30 * mm
    for i, sku in enumerate(PICK):
        x = 15 * mm + (i % cols) * cw
        y = H - 25 * mm - (i // cols + 1) * ch
        c = cat[sku]
        pdf.setFont("Helvetica", 8)
        pdf.drawString(x + 2 * mm, y + ch - 5 * mm, f"{name(c)[:34]}  Rs {c['selling_price']}")
        bc = eanbc.Ean13BarcodeWidget(ean[sku])
        bc.barHeight = 14 * mm
        bc.barWidth = 0.33 * mm
        x0, y0, x1, y1 = bc.getBounds()
        dr = Drawing(x1 - x0, y1 - y0)
        dr.add(bc)
        renderPDF.draw(dr, pdf, x + 4 * mm, y + 2 * mm)
    pdf.save()

    (OUT / "README.txt").write_text(f"""ShelfSense — demo with a new shop (about 3 minutes)

1. Open ShelfSense and tap Sign up. Use any 10-digit mobile number (not 1234567890) and a 4–6 digit PIN.
   The new shop starts empty.
2. Stock tab -> Start shelf count.
   - Import a stock list (CSV) -> choose demo_opening_stock.csv -> check the numbers -> Save count.
   - Or scan: tap Camera and point it at the barcodes in demo_barcodes.pdf (printed or on another screen).
     Each scan adds 1. A USB barcode scanner also works: click the box and scan.
3. Billing station -> Choose CSV file -> demo_sales_week.csv.
   The sales are dated {(end - timedelta(days=6)).strftime('%d %b')}–{end.strftime('%d %b %Y')}. If they are older than your count, tick
   "Take those sales off stock too" -> Use these sales. The Stock tab now shows days left per product.
4. Billing station -> Paytm payments:
   - Tap "Listen to Soundbox" (Chrome on Android, microphone allowed) and play a Soundbox payment, or say
     "Paytm par 45 rupaye prapt hue" / "Received rupees 45 on Paytm".
   - Or type an amount such as 45 and tap Add.
   The app shows the most likely items for that amount. Confirm, pick another guess, Edit items, or mark Not a sale.
5. Stock tab: products expiring within 3 days appear under "Use soon".

Admin example shop (practice orders, deliveries and bill photo): mobile 1234567890, PIN 3456.
""")
    print("wrote", ", ".join(p.name for p in sorted(OUT.iterdir())))


if __name__ == "__main__":
    main()
