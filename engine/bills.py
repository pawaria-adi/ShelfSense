"""
ShelfSense — step 5: read a delivery bill, check it against the order, confirm it.

When stock arrives, the distributor's bill says what was actually delivered and
at what price. Checking it against what was ordered catches the everyday
problems: short deliveries, missing items, substitutes, surprise price rises.
Once the shopkeeper confirms, the delivered quantities are added to stock.

USAGE
    python3 engine/bills.py read  data/bills/gupta_18-09-2026.txt
    python3 engine/bills.py read  data/bills/metro_18-09-2026.png   (photo: needs tesseract)
    python3 engine/bills.py confirm INV/GT/4471        (after checking the report)
    python3 engine/bills.py list

Bills can be text, CSV, PDF (needs pdftotext) or a photo (needs the free
`tesseract` OCR program). On the phone screen, bill photos are read by Claude
instead, so nothing needs installing there.

Each bill becomes data/events/deliveries/<supplier>_<invoice>.json with
status "needs_review" until confirmed; only confirmed deliveries change stock.
"""

import json, re, shutil, subprocess, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pos_connector import Matcher, parse_date  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / "data", ROOT / "out"
DELIVERIES = DATA / "events" / "deliveries"
PRICE_TOLERANCE = 0.005          # 0.5% before a price change is flagged
NUM = r"-?\d+(?:,\d{3})*(?:\.\d+)?"
# an expiry printed on a bill line: "EXP 03/27", "12-2026", "31/03/2027"
EXPIRY = re.compile(r"(?:EXP(?:IRY)?\.?:?\s*)?\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}[/-]\d{2,4})\b", re.I)


def expiry_iso(token):
    """'31/03/2027' -> 2027-03-31; '03/27' or '3-2027' -> last day of that month."""
    import calendar
    parts = re.split(r"[/-]", token)
    try:
        if len(parts) == 3:
            return parse_date(token.replace("-", "/"))
        m, y = int(parts[0]), int(parts[1])
        y = y + 2000 if y < 100 else y
        if not 1 <= m <= 12:
            return None
        return f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"
    except (ValueError, IndexError):
        return None


# --------------------------------------------------------------------------
# getting text out of the file
# --------------------------------------------------------------------------
def _ocr(src):
    raw = subprocess.run(["tesseract", str(src), "-", "--psm", "6"], capture_output=True,
                         text=True, check=True).stdout
    raw = re.sub(r"[«»=|()\[\]{}~_]", " ", raw)          # OCR specks
    return re.sub(r"(\d) +\.(\d)", r"\1.\2", raw)       # "1008 .00" -> "1008.00"


def bill_texts(path):
    """Candidate readings of one file (a photo gets two: as-is and enlarged)."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".txt", ".csv"):
        return [path.read_text(encoding="utf-8", errors="replace")]
    if ext == ".pdf":
        if not shutil.which("pdftotext"):
            raise SystemExit("Reading PDF bills needs 'pdftotext' (poppler). Or upload it on the phone screen.")
        return [subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True,
                               text=True, check=True).stdout]
    if ext in (".png", ".jpg", ".jpeg", ".webp"):
        if not shutil.which("tesseract"):
            raise SystemExit("Reading bill photos here needs 'tesseract' (free OCR). "
                             "Or upload the photo on the phone screen, where Claude reads it.")
        texts = [_ocr(path)]
        try:                                   # small print often reads better enlarged
            from PIL import Image, ImageOps
            import tempfile
            img = ImageOps.grayscale(Image.open(path))
            img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
            with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                img.save(tmp.name)
                texts.append(_ocr(tmp.name))
        except ImportError:
            pass
        return texts
    raise SystemExit(f"Don't know how to read {ext} bills")


def best_parse(path, suppliers):
    """Parse every reading and keep the one whose lines add up to the printed total."""
    def score(b):
        lines_total = sum(i["amount"] for i in b["items"])
        adds_up = b["bill_total"] is not None and abs(lines_total - b["bill_total"]) <= 1
        return (adds_up, len(b["items"]))
    return max((parse_bill(t, suppliers) for t in bill_texts(path)), key=score)


# --------------------------------------------------------------------------
# understanding the text
# --------------------------------------------------------------------------
def to_num(t):
    return float(t.replace(",", ""))


def parse_bill(text, suppliers):
    lines = [l.rstrip() for l in text.splitlines()]
    head = " ".join(lines[:12]).upper()
    supplier = next((s for s in suppliers if s["name"].upper() in head), None)
    inv = re.search(r"INVOICE\s*(?:NO|#)\.?\s*[:\-]?\s*([A-Z0-9/\-]+)", head)
    date = re.search(r"DATE\s*[:\-]?\s*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})", head)
    bill_date = None
    if date:
        try:
            bill_date = parse_date(date.group(1))
        except ValueError:
            bill_date = None                      # misread; the shopkeeper fills it in
    total = None
    items = []
    for l in lines:
        up = l.upper()
        if "TOTAL" in up:
            nums = re.findall(NUM, l)
            if nums:
                total = to_num(nums[-1])
            continue
        m = re.match(r"^\s*\d{1,3}[.)]?\s+(.*)$", l)
        if not m:
            continue
        body = m.group(1)
        exp = None
        em = EXPIRY.search(body)
        if em:
            exp = expiry_iso(em.group(1))
            body = body[:em.start()] + " " + body[em.end():]
        tokens = body.split()
        # the numbers at the END of the line are HSN / GST / qty / rate / amount;
        # everything before them is the item name ("CADBURY 5 STAR 40G" keeps its 5)
        k = len(tokens)
        while k > 0 and re.fullmatch(NUM, tokens[k - 1]):
            k -= 1
        if k == 0 or len(tokens) - k < 3:
            continue
        name = " ".join(tokens[:k])
        nums = [to_num(t) for t in tokens[k:] if re.fullmatch(NUM, t)]
        # find qty, rate, amount: the last three numbers where qty x rate ~= amount
        found = None
        for i in range(len(nums) - 2, -1, -1):
            for j in range(i):
                q, r, a = nums[j], nums[i], nums[i + 1]
                if q > 0 and abs(q * r - a) <= max(1.0, 0.01 * a):
                    found = (q, r, a)
                    break
            if found:
                break
        if found:
            items.append({"text": name, "qty": found[0], "rate": found[1], "amount": found[2],
                          "expiry": exp})
    return {
        "supplier_id": supplier["supplier_id"] if supplier else None,
        "supplier": supplier["name"] if supplier else None,
        "invoice_no": inv.group(1) if inv else None,
        "bill_date": bill_date,
        "bill_total": total,
        "items": items,
    }


# --------------------------------------------------------------------------
# checking against the order
# --------------------------------------------------------------------------
def check(bill, order_lines, cards, matcher):
    """order_lines: [{sku_id, qty}] for this supplier. Returns lines + issues."""
    ordered = defaultdict(float)
    for o in order_lines:
        ordered[o["sku_id"]] += o["qty"]
    billed = defaultdict(float)
    lines, issues = [], []
    for it in bill["items"]:
        sku, score = matcher.match("", it["text"])
        line = dict(it, sku_id=sku, match_score=score)
        if not sku:
            issues.append({"type": "unknown_item", "text": it["text"], "qty": it["qty"],
                           "say": f"'{it['text']}' is not in your product list (substitute?). "
                                  f"Add it or return it."})
        else:
            name = matcher.catalogue[sku]
            line["name"] = f"{name['brand']} {name['product_name']} {name['pack_size']}"
            billed[sku] += it["qty"]
            card = cards.get((bill["supplier_id"], sku))
            if card and abs(it["rate"] - card["unit_cost"]) <= PRICE_TOLERANCE * card["unit_cost"]:
                # within a whisker of the agreed price: a misread digit, not a price change
                line["rate"] = card["unit_cost"]
                line["amount"] = round(it["qty"] * card["unit_cost"], 2)
            elif card:
                up = it["rate"] > card["unit_cost"]
                issues.append({"type": "price_up" if up else "price_down", "sku_id": sku,
                               "expected": card["unit_cost"], "billed": it["rate"],
                               "extra_cost": round((it["rate"] - card["unit_cost"]) * it["qty"], 2),
                               "say": f"{line['name']}: Rs {it['rate']:.2f} a piece, agreed "
                                      f"Rs {card['unit_cost']:.2f} ({'+' if up else ''}"
                                      f"{(it['rate'] / card['unit_cost'] - 1) * 100:.1f}%)."})
        lines.append(line)
    for sku in set(ordered) | set(billed):
        o, b = ordered.get(sku, 0), billed.get(sku, 0)
        c = matcher.catalogue[sku]
        name = f"{c['brand']} {c['product_name']} {c['pack_size']}"
        if o and not b:
            issues.append({"type": "missing", "sku_id": sku, "ordered": o, "billed": 0,
                           "say": f"{name}: ordered {o:g}, not on the bill. Reorder it today."})
        elif o and b < o:
            issues.append({"type": "short", "sku_id": sku, "ordered": o, "billed": b,
                           "say": f"{name}: ordered {o:g}, bill says {b:g}. Count the boxes."})
        elif o and b > o:
            issues.append({"type": "over", "sku_id": sku, "ordered": o, "billed": b,
                           "say": f"{name}: ordered {o:g}, bill says {b:g}."})
        elif b and not o:
            issues.append({"type": "not_ordered", "sku_id": sku, "ordered": 0, "billed": b,
                           "say": f"{name}: {b:g} on the bill but not ordered. Keep or send back?"})
    computed = round(sum(l["amount"] for l in lines), 2)
    if bill["bill_total"] is not None and abs(computed - bill["bill_total"]) > 1:
        issues.append({"type": "total_mismatch", "billed": bill["bill_total"], "computed": computed,
                       "say": f"Bill total Rs {bill['bill_total']:,.2f} but the lines add up to "
                              f"Rs {computed:,.2f}. Ask the distributor."})
    order_ = {"missing": 0, "short": 1, "unknown_item": 2, "price_up": 3, "not_ordered": 4,
              "over": 5, "total_mismatch": 6, "price_down": 7}
    issues.sort(key=lambda i: order_.get(i["type"], 9))
    return lines, issues, dict(billed), computed


# --------------------------------------------------------------------------
def expiry_by_sku(lines):
    """Earliest printed expiry per product (stock.py falls back to shelf life)."""
    out = {}
    for l in lines:
        if l.get("sku_id") and l.get("expiry"):
            out[l["sku_id"]] = min(out.get(l["sku_id"], l["expiry"]), l["expiry"])
    return out


def load_context():
    sup = json.loads((DATA / "suppliers.json").read_text())
    cards = {(c["supplier_id"], c["sku_id"]): c for c in sup["price_cards"]}
    return sup["suppliers"], cards


def order_for(supplier_name, bill_date=None):
    """The most recent order to this supplier placed before the bill (same day if none)."""
    files = sorted((OUT / "orders").glob("orders_*.json"))
    dated = [(f.stem.split("_", 1)[1], f) for f in files]
    if bill_date:
        before = [x for x in dated if x[0] < bill_date] or [x for x in dated if x[0] == bill_date]
    else:
        before = dated
    for _, f in reversed(before):
        rows = [r for r in json.loads(f.read_text())["lines"] if r["buy_from"] == supplier_name]
        if rows:
            return [{"sku_id": r["sku_id"], "qty": r["qty"]} for r in rows]
    return []


def event_path(bill):
    safe = re.sub(r"[^A-Za-z0-9]+", "-", bill["invoice_no"] or "unknown").strip("-")
    return DELIVERIES / f"{bill['supplier_id'] or 'UNKNOWN'}_{safe}.json"


def read(paths):
    """One bill; several paths = several pages of the same bill."""
    paths = [paths] if isinstance(paths, (str, Path)) else list(paths)
    suppliers, cards = load_context()
    matcher = Matcher()
    pages = [best_parse(p, suppliers) for p in paths]
    bill = pages[0]
    for pg in pages[1:]:
        bill["items"] += pg["items"]
        for k in ("supplier_id", "supplier", "invoice_no", "bill_date", "bill_total"):
            bill[k] = pg[k] if pg[k] is not None and (k == "bill_total" or bill[k] is None) else bill[k]
    path = paths[0]
    if not bill["supplier_id"]:
        raise SystemExit("Couldn't tell which supplier this bill is from. Check the file.")
    lines, issues, received, computed = check(bill, order_for(bill["supplier"], bill["bill_date"]), cards, matcher)
    event = {
        "kind": "delivery", "status": "needs_review",
        "supplier_id": bill["supplier_id"], "supplier": bill["supplier"],
        "invoice_no": bill["invoice_no"], "bill_date": bill["bill_date"],
        "bill_total": bill["bill_total"], "lines_total": computed,
        "received": received, "lines": lines, "issues": issues,
        "expiry": expiry_by_sku(lines),
        "source_file": ", ".join(Path(p).name for p in paths), "read_at": datetime.now().isoformat(timespec="seconds"),
    }
    DELIVERIES.mkdir(parents=True, exist_ok=True)
    p = event_path(bill)
    if p.exists() and json.loads(p.read_text()).get("status") == "confirmed":
        print(f"Invoice {bill['invoice_no']} is already confirmed; not overwriting.")
        return json.loads(p.read_text())
    p.write_text(json.dumps(event, indent=1))
    report(event)
    return event


def report(e):
    print(f"\n{e['supplier']} — invoice {e['invoice_no']} ({e['bill_date']})  [{e['status']}]")
    print(f"  {len(e['lines'])} lines, {sum(e['received'].values()):g} pieces matched, "
          f"bill total Rs {e['bill_total'] or 0:,.2f}")
    if not e["issues"]:
        print("  Everything matches the order.")
    for i in e["issues"]:
        print(f"  ! {i['say']}")
    if e["status"] != "confirmed":
        print(f"  -> After checking the boxes: python3 engine/bills.py confirm {e['invoice_no']}")


def confirm(invoice_no, overrides=None):
    for p in sorted(DELIVERIES.glob("*.json")):
        e = json.loads(p.read_text())
        if e["invoice_no"] == invoice_no:
            if overrides:
                e["received"].update(overrides)
            e["status"] = "confirmed"
            e["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
            p.write_text(json.dumps(e, indent=1))
            print(f"Confirmed {invoice_no}: {sum(e['received'].values()):g} pieces will be added to stock.")
            return e
    raise SystemExit(f"No bill with invoice number {invoice_no}. Run 'read' first.")


def main(argv):
    if len(argv) >= 2 and argv[0] == "read":
        groups = defaultdict(list)                 # gupta_p1.png + gupta_p2.png -> one bill
        for p in argv[1:]:
            groups[re.sub(r"_p\d+$", "", Path(p).stem)].append(p)
        for ps in groups.values():
            read(sorted(ps))
    elif len(argv) >= 2 and argv[0] == "confirm":
        for inv in argv[1:]:
            confirm(inv)
    elif argv and argv[0] == "list":
        for p in sorted(DELIVERIES.glob("*.json")):
            e = json.loads(p.read_text())
            print(f"{e['status']:<13} {e['bill_date']}  {e['supplier']:<20} {e['invoice_no']:<16} "
                  f"{len(e['issues'])} issue(s)")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
