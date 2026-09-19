"""
ShelfSense — step 5: what is on the shelf right now.

    on shelf now = last shelf count
                 - what sold since   (exact from a billing station if connected,
                                      otherwise the step-2 estimate + ONDC orders)
                 + deliveries the shopkeeper confirmed from their bills

USAGE
    python3 engine/stock.py                      -> out/stock_now.json, out/stock_report.txt
    python3 engine/stock.py import-screen FILE   bring in deliveries and scanner sales
                                                 saved from the phone screen

out/stock_now.json has the same shape as data/stock_count.json, so the reorder
helper can plan from it:
    python3 engine/reorder.py --stock out/stock_now.json
"""

import json, sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shelf import add_days, consume, drop_expired, lots_total, shelf_days  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / "data", ROOT / "out"
EVENTS = DATA / "events"
POS_FILES = (EVENTS / "pos_sales.json", EVENTS / "screen_scans.json")


def day_range(start, end):
    d = date.fromisoformat(start)
    while d.isoformat() < end:
        yield d.isoformat()
        d += timedelta(days=1)


def pos_days():
    """Exact sales per day from any connected billing station (file import, live feed, phone scanner)."""
    days = defaultdict(lambda: defaultdict(float))
    for f in POS_FILES:
        if f.exists():
            s = json.loads(f.read_text())
            for day, units in s.get("days", {}).items():
                for sku, q in units.items():
                    days[day][sku] += q
    return days


def confirmed_deliveries():
    out = []
    for p in sorted((EVENTS / "deliveries").glob("*.json")):
        e = json.loads(p.read_text())
        if e.get("status") == "confirmed" and e.get("bill_date"):
            out.append(e)
    return out


def build():
    catalogue = {c["sku_id"]: c for c in json.loads((DATA / "catalogue.json").read_text())}
    count = json.loads((DATA / "stock_count.json").read_text())
    d0 = count["count_date"]
    ledger = {r["sku_id"]: r for r in json.loads((OUT / "ledger.json").read_text())}
    n_days = 30
    walk_rate = {s: r["estimated_walkin_units"] / n_days for s, r in ledger.items()}
    ondc_rate = {s: r["confirmed_units_ondc"] / n_days for s, r in ledger.items()}
    ondc = defaultdict(lambda: defaultdict(float))
    ondc_days = set()
    for o in json.loads((DATA / "ondc_orders.json").read_text()):
        day = o["timestamp"][:10]
        ondc_days.add(day)
        for it in o["items"]:
            ondc[day][it["sku_id"]] += it["qty"]
    pos = pos_days()
    wf_file = EVENTS / "writeoffs.json"                 # pieces removed on the phone screen
    writeoffs = defaultdict(list)
    for w in (json.loads(wf_file.read_text()).get("entries", []) if wf_file.exists() else []):
        writeoffs[w["at"][:10]].append(w)
    deliveries = [e for e in confirmed_deliveries() if e["bill_date"] >= d0]

    # the stock picture is for the MORNING of as_of
    candidates = [d0] + [e["bill_date"] for e in deliveries]
    candidates += [(date.fromisoformat(d) + timedelta(days=1)).isoformat() for d in pos if d >= d0]
    as_of = max(candidates)

    counted = {c["sku_id"]: float(c["on_hand"]) for c in count["counts"]}
    # batches behind the count: from data/stock_lots.json if it matches, else one estimated batch
    lots_file = DATA / "stock_lots.json"
    known = json.loads(lots_file.read_text()) if lots_file.exists() else {}
    known = known.get("lots", {}) if known.get("count_date") == d0 else {}
    lots = {}
    for sku, q in counted.items():
        if q <= 0:
            lots[sku] = []
        elif known.get(sku) and abs(lots_total(known[sku]) - q) < 0.5:
            lots[sku] = [dict(l) for l in known[sku]]
        else:
            lots[sku] = [{"qty": q, "exp": add_days(d0, max(1, shelf_days(sku) // 2)), "est": True}]
    sold = defaultdict(float)
    received = defaultdict(float)
    overdrawn = set()          # billing station sold more than the count allowed
    expired_removed = []
    removed = defaultdict(float)
    source_days = {"billing_station": [], "estimate": []}
    arrivals = defaultdict(list)
    for e in deliveries:
        arrivals[e["bill_date"]].append(e)

    def receive(day):
        for e in arrivals.get(day, []):          # deliveries land in the morning
            for sku, q in e["received"].items():
                exp = (e.get("expiry") or {}).get(sku) or add_days(day, shelf_days(sku))
                lots.setdefault(sku, []).append({"qty": q, "exp": exp, "est": sku not in (e.get("expiry") or {})})
                received[sku] += q

    def expire(day):
        for sku, ls in lots.items():
            for l in drop_expired(ls, day):
                expired_removed.append({"sku_id": sku, "qty": round(l["qty"], 1), "exp": l["exp"]})

    for day in day_range(d0, as_of):
        receive(day)
        for w in writeoffs.get(day, []):          # removals first, so an expired batch the
            ls = lots.setdefault(w["sku"], [])     # shopkeeper already took off isn't counted twice
            match = [l for l in ls if l["exp"] == w.get("exp")]
            consume(match or ls, w["qty"])
            ls[:] = [l for l in ls if l["qty"] > 1e-9]
            removed[w["sku"]] += w["qty"]
        expire(day)
        wanted = defaultdict(float)
        exact = day in pos
        if exact:
            source_days["billing_station"].append(day)
            for sku, q in pos[day].items():
                wanted[sku] += q
        else:
            source_days["estimate"].append(day)
            for sku, r in walk_rate.items():
                wanted[sku] += r
        if day in ondc_days:
            for sku, q in ondc[day].items():
                wanted[sku] += q
        elif not exact:
            for sku, r in ondc_rate.items():
                wanted[sku] += r
        for sku, q in wanted.items():
            have = lots_total(lots.get(sku, []))
            if q > have + 0.5 and exact:
                overdrawn.add(sku)
            take = min(q, have)                  # nothing sells from an empty shelf
            consume(lots.setdefault(sku, []), take)
            sold[sku] += q if exact else take
    receive(as_of)
    expire(as_of)

    detail = []
    for sku, c in catalogue.items():
        ls = sorted(lots.get(sku, []), key=lambda l: l["exp"])
        detail.append({
            "sku_id": sku, "name": f"{c['brand']} {c['product_name']} {c['pack_size']}",
            "counted": counted.get(sku, 0.0), "sold_since": round(sold[sku], 1),
            "received": received[sku], "removed": removed[sku], "on_hand": int(round(lots_total(ls))),
            "recount": sku in overdrawn,
            "next_expiry": ls[0]["exp"] if ls else None,
        })
    return {
        "count_date": as_of,
        "based_on_count": d0,
        "sales_source_days": source_days,
        "deliveries_applied": [f"{e['supplier']} {e['invoice_no']}" for e in deliveries],
        "counts": [{"sku_id": d["sku_id"], "on_hand": d["on_hand"]} for d in detail],
        "lots": {sku: [dict(l, qty=round(l["qty"], 1)) for l in sorted(ls, key=lambda l: l["exp"])]
                 for sku, ls in lots.items() if ls},
        "expired_removed": expired_removed,
        "detail": detail,
    }


def report(s):
    lines = [
        f"STOCK ON THE MORNING OF {s['count_date']}",
        f"  starts from the shelf count of {s['based_on_count']}",
        f"  sales: {len(s['sales_source_days']['billing_station'])} day(s) from the billing station, "
        f"{len(s['sales_source_days']['estimate'])} day(s) estimated from payments",
        f"  deliveries added: {', '.join(s['deliveries_applied']) or 'none'}",
        "",
        f"{'item':<36}{'counted':>8}{'sold':>8}{'in':>6}{'out':>6}{'now':>6}",
    ]
    if s.get("expired_removed"):
        lines.insert(4, f"  expired and taken out of stock: {len(s['expired_removed'])} batch(es) "
                        "(see out/expiry_report.txt)")
    changed = [d for d in s["detail"] if d["received"] or d["recount"] or d.get("removed")]
    for d in sorted(changed, key=lambda d: d["name"]):
        flag = "  <- billed more than was counted: recount" if d["recount"] else ""
        lines.append(f"{d['name'][:35]:<36}{d['counted']:>8.0f}{d['sold_since']:>8.1f}"
                     f"{d['received']:>6.0f}{d.get('removed', 0):>6.0f}{d['on_hand']:>6}{flag}")
    lines.append(f"\n(in = delivered, out = removed on the phone: expired, damaged, returned)\n"
                 f"{len(s['detail']) - len(changed)} other items: counted minus sold, nothing received.")
    neg = sum(d["recount"] for d in s["detail"])
    if neg:
        lines.append(f"{neg} item(s) were billed more times than the shelf count allowed. "
                     "A quick recount of those fixes the count.")
    if s["sales_source_days"]["estimate"]:
        lines.append("Sales on estimated days are averages; expect a few pieces of drift "
                     "per item until the weekly recount.")
    return "\n".join(lines)


def import_screen(path):
    """Bring in what the phone screen recorded (its 'Save for the computer' file)."""
    x = json.loads(Path(path).read_text())
    ddir = EVENTS / "deliveries"
    ddir.mkdir(parents=True, exist_ok=True)
    added = 0
    for e in x.get("deliveries", []):
        inv = "".join(ch if ch.isalnum() else "-" for ch in (e.get("invoice_no") or e.get("id", "screen")))
        p = ddir / f"{e.get('supplier_id', 'UNKNOWN')}_{inv.strip('-')}.json"
        if p.exists() and json.loads(p.read_text()).get("status") == "confirmed":
            continue
        e.setdefault("kind", "delivery")
        e.setdefault("source_file", Path(path).name)
        p.write_text(json.dumps(e, indent=1))
        added += 1
    wo = x.get("writeoffs", [])
    if wo:      # the screen keeps the full list, so replace
        (EVENTS / "writeoffs.json").write_text(json.dumps({"entries": wo}, indent=1))
    scans = x.get("scanner_sales", {}).get("days", {})
    if scans:   # the screen keeps its full history, so replace rather than add
        (EVENTS / "screen_scans.json").write_text(json.dumps({"days": scans}, indent=1))
    print(f"imported {added} delivery bill(s), {len(scans)} day(s) of phone-scanner sales, "
          f"{len(wo)} removed-stock entr{'y' if len(wo) == 1 else 'ies'}")


def main(argv):
    if len(argv) == 2 and argv[0] == "import-screen":
        import_screen(argv[1])
        return
    s = build()
    OUT.mkdir(exist_ok=True)
    (OUT / "stock_now.json").write_text(json.dumps(s, indent=1))
    text = report(s)
    (OUT / "stock_report.txt").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main(sys.argv[1:])
