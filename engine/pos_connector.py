"""
ShelfSense — step 5: connect a billing / scanning station.

Shops that bill on a counter PC or tablet with a barcode scanner already know
exactly what they sold. This module brings those sales into ShelfSense, which
then uses them instead of guessing from Paytm payments.

Three ways in, from simplest to most "live":

  1. IMPORT A DAILY EXPORT (works with almost any billing software)
       python3 engine/pos_connector.py import data/pos/<export>.csv
     Columns are detected by name (barcode / item code, item name, qty, date).
     Importing the same file twice does not double-count.

  2. USB OR BLUETOOTH SCANNER ON THE PHONE SCREEN
     Handheld scanners "type" the barcode followed by Enter. The shopkeeper
     screen has a Scan tab that listens for that. No software needed.

  3. LIVE FEED FROM BILLING SOFTWARE ON THE SAME WI-FI
       python3 engine/pos_connector.py serve            (listens on port 8765)
     The billing software (or a small plug-in) sends each finished bill:
       curl -X POST http://<this-computer>:8765/sale \\
            -H 'Content-Type: application/json' \\
            -d '{"bill_no":"B123","items":[{"barcode":"8901234567890","qty":2}]}'
     GET /status shows what has been received today.

Everything lands in data/events/pos_sales.json:
    {"days": {"2026-09-16": {"SKU001": 12, ...}},
     "unmatched": {"2026-09-16": {"LOOSE ITEM": 3}},
     "imported": {"<file fingerprint>": "<file name>"},
     "live_bills": ["B123", ...]}
"""

import csv, difflib, hashlib, json, re, sys, threading
from collections import defaultdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EVENTS = DATA / "events"
STORE = EVENTS / "pos_sales.json"
LOCK = threading.Lock()

HEADER_HINTS = {
    "barcode": ("barcode", "ean", "item code", "itemcode", "sku code", "product code", "upc"),
    "name":    ("item name", "item", "product", "description", "particulars"),
    "qty":     ("qty", "quantity", "units", "pcs"),
    "date":    ("bill date", "date", "invoice date"),
}


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------
class Matcher:
    """Turns a barcode or a printed item name into one of our SKUs."""

    def __init__(self):
        self.catalogue = {c["sku_id"]: c for c in json.loads((DATA / "catalogue.json").read_text())}
        self.by_code = json.loads((DATA / "barcodes.json").read_text())
        self.names = {self.norm(f"{c['brand']} {c['product_name']} {c['pack_size']}"): s
                      for s, c in self.catalogue.items()}

    ABBREV = {"BISC": "BISCUITS", "DET": "DETERGENT", "PWD": "POWDER", "SHMP": "SHAMPOO",
              "NDL": "NOODLES", "SD": "SOFT DRINK", "LTR": "L", "GM": "G", "GMS": "G"}

    @classmethod
    def norm(cls, text):
        """Upper-case, drop punctuation, expand distributor abbreviations."""
        words = re.sub(r"[^A-Z0-9]+", " ", str(text).upper().replace("'", "")).split()
        return " ".join(cls.ABBREV.get(w, w) for w in words)

    def match(self, barcode="", name="", cutoff=0.82):
        code = re.sub(r"\D", "", str(barcode or ""))
        if code in self.by_code:
            return self.by_code[code], 1.0
        n = self.norm(name or "")
        if not n:
            return None, 0.0
        if n in self.names:
            return self.names[n], 1.0
        best = difflib.get_close_matches(n, self.names, n=1, cutoff=cutoff)
        if best:
            return self.names[best[0]], round(difflib.SequenceMatcher(None, n, best[0]).ratio(), 2)
        return None, 0.0


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------
def load_store():
    if STORE.exists():
        return json.loads(STORE.read_text())
    return {"days": {}, "unmatched": {}, "imported": {}, "live_bills": []}


def save_store(s):
    EVENTS.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(s, indent=1, sort_keys=True))


def add_units(store, day, sku, qty, label=None):
    if sku:
        d = store["days"].setdefault(day, {})
        d[sku] = d.get(sku, 0) + qty
    else:
        u = store["unmatched"].setdefault(day, {})
        u[label or "?"] = u.get(label or "?", 0) + qty


def parse_date(text):
    text = str(text).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text[:11].strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"can't read the date '{text}'")


# --------------------------------------------------------------------------
# 1. import
# --------------------------------------------------------------------------
def find_columns(header):
    low = [h.strip().lower() for h in header]
    cols = {}
    for key, hints in HEADER_HINTS.items():
        for hint in hints:                       # most specific hint first
            hit = next((i for i, h in enumerate(low) if h == hint), None)
            if hit is None:
                hit = next((i for i, h in enumerate(low) if hint in h and i not in cols.values()), None)
            if hit is not None:
                cols[key] = hit
                break
    missing = [k for k in ("qty", "date") if k not in cols]
    if missing or ("barcode" not in cols and "name" not in cols):
        raise ValueError(f"export is missing columns: {missing or ['barcode or item name']}; "
                         f"found {header}")
    return cols


def import_file(path, matcher=None):
    path = Path(path)
    raw = path.read_bytes()
    fp = hashlib.sha256(raw).hexdigest()[:16]
    with LOCK:
        store = load_store()
        if fp in store["imported"]:
            return {"file": path.name, "skipped": "already imported"}
    matcher = matcher or Matcher()
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    cols = find_columns(rows[0])
    per_day, unmatched, lines = defaultdict(lambda: defaultdict(float)), defaultdict(lambda: defaultdict(float)), 0
    fuzzy = {}
    for r in rows[1:]:
        if not r or not any(r):
            continue
        day = parse_date(r[cols["date"]])
        qty = float(r[cols["qty"]] or 0)
        code = r[cols["barcode"]] if "barcode" in cols else ""
        name = r[cols["name"]] if "name" in cols else ""
        sku, score = matcher.match(code, name)
        if sku and score < 1.0:
            fuzzy[name] = (sku, score)
        if sku:
            per_day[day][sku] += qty
        else:
            unmatched[day][name or code or "?"] += qty
        lines += 1
    with LOCK:
        store = load_store()
        for day, units in per_day.items():
            for sku, q in units.items():
                add_units(store, day, sku, q)
        for day, units in unmatched.items():
            for label, q in units.items():
                add_units(store, day, None, q, label)
        store["imported"][fp] = path.name
        save_store(store)
    total = sum(sum(u.values()) for u in per_day.values())
    miss = sum(sum(u.values()) for u in unmatched.values())
    return {"file": path.name, "lines": lines, "days": len(per_day),
            "units_matched": total, "units_unmatched": miss,
            "match_rate_pct": round(100 * total / (total + miss), 1) if total + miss else 0,
            "fuzzy_matches": len(fuzzy)}


# --------------------------------------------------------------------------
# 3. live feed
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    matcher = None

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/") != "/status":
            return self._send(404, {"error": "try GET /status or POST /sale"})
        today = datetime.now().date().isoformat()
        with LOCK:
            s = load_store()
        units = s["days"].get(today, {})
        self._send(200, {"date": today, "items_today": len(units),
                         "units_today": sum(units.values()), "bills_received": len(s["live_bills"])})

    def do_POST(self):
        if self.path.rstrip("/") != "/sale":
            return self._send(404, {"error": "POST bills to /sale"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            items = body.get("items") or [body]
            day = parse_date(body["date"]) if body.get("date") else datetime.now().date().isoformat()
            bill = str(body.get("bill_no") or "")
        except (ValueError, KeyError, TypeError) as e:
            return self._send(400, {"error": f"couldn't read the bill: {e}"})
        result = []
        with LOCK:
            s = load_store()
            if bill and bill in s["live_bills"]:
                return self._send(200, {"ok": True, "duplicate": True, "bill_no": bill})
            for it in items:
                qty = float(it.get("qty", 1))
                sku, score = self.matcher.match(it.get("barcode", ""), it.get("name", ""))
                add_units(s, day, sku, qty, it.get("name") or it.get("barcode"))
                result.append({"barcode": it.get("barcode"), "sku_id": sku, "qty": qty})
            if bill:
                s["live_bills"] = (s["live_bills"] + [bill])[-5000:]
            save_store(s)
        self._send(200, {"ok": True, "date": day, "items": result,
                         "unmatched": sum(1 for r in result if not r["sku_id"])})

    def log_message(self, fmt, *args):
        sys.stderr.write("[billing-station] " + fmt % args + "\n")


def serve(port=8765):
    Handler.matcher = Matcher()
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Listening for bills on port {port}. POST /sale, GET /status. Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def main(argv):
    if len(argv) >= 2 and argv[0] == "import":
        m = Matcher()
        for p in argv[1:]:
            print(json.dumps(import_file(p, m)))
    elif argv and argv[0] == "serve":
        serve(int(argv[1]) if len(argv) > 1 else 8765)
    elif argv and argv[0] == "status":
        s = load_store()
        days = sorted(s["days"])
        print(f"{len(days)} days of billing data" + (f" ({days[0]} to {days[-1]})" if days else ""))
        print(f"files imported: {len(s['imported'])}, live bills: {len(s['live_bills'])}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
