"""
ShelfSense — step 3: what to reorder today, and from whom.

INPUTS
    out/ledger.json          estimated daily sales per SKU (popularity method, step 2)
    data/stock_count.json    what is on the shelf this morning
    data/suppliers.json      3 distributors: price, minimum order, credit, speed, reliability
    data/catalogue.json      MRP and category per SKU

THE RULES (plain version)
    1. Days left = units on shelf / units sold per day.
    2. If an item will run out before a normal delivery could arrive plus a safety
       cushion, it goes on today's list.
    3. Order enough to last TARGET_COVER_DAYS after delivery, rounded up to whole
       cartons (or loose packs of 6 when a carton is too much), never more than
       3 weeks of stock, and never more than a fresh item can sell before it spoils
       (shelf lives in data/shelf_life.json).
    4. For each item, price every supplier on the TRUE cost of using them:
         what you pay
       + profit lost on sales you miss while waiting for delivery
       + the cost of money stuck in extra stock
       + stock that expires unsold
       - the value of paying later (credit)
       and pick the cheapest per usable unit.
    5. Group the picks into one order per supplier, written as a WhatsApp message.

HOW WE TEST IT
    A 14-day simulation of the shop, repeated 200 times with random demand drawn
    from the TRUE sales rates. The app only ever sees its own estimates. Its stock
    count drifts between the weekly recounts, as it would in real life.
    Three shopkeepers are compared:
      - ShelfSense           (uses the estimates)
      - Perfect knowledge    (same rules, true sales rates: the best these rules can do)
      - Gut feel             (reorders 1 carton from the usual supplier when under 3 units)
"""

import argparse, json, math, random, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shelf import stocking_limit  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / "data", ROOT / "out"

TARGET_COVER_DAYS = 7          # order enough for about a week after delivery
SAFETY_DAYS = 1.5              # cushion for busy days and estimate error
REVIEW_DAYS = 1                # the shopkeeper orders once a day
CAPITAL_COST_YEAR = 0.18       # what money costs a small shop per year (informal credit)
HOLD_COST_DAY = CAPITAL_COST_YEAR / 365
MAX_STOCK_DAYS = 21           # never hold more than 3 weeks of an item (cash + shelf space)
INNER_PACK = 6                 # non-bulk distributors will sell slow items in packs of 6
RECOUNT_EVERY = 7              # simulated weekly shelf recount
SIM_DAYS, SIM_RUNS = 14, 200


# --------------------------------------------------------------------------
# LOADING
# --------------------------------------------------------------------------
def load(stock_path=None, sales="estimate"):
    catalogue = {c["sku_id"]: c for c in json.loads((DATA / "catalogue.json").read_text())}
    sup = json.loads((DATA / "suppliers.json").read_text())
    suppliers = {s["supplier_id"]: s for s in sup["suppliers"]}
    cards = defaultdict(dict)                      # sku -> supplier -> card
    for c in sup["price_cards"]:
        cards[c["sku_id"]][c["supplier_id"]] = c
    ledger_file = OUT / ("ledger_pos.json" if sales == "billing_station" else "ledger.json")
    ledger = {r["sku_id"]: r for r in json.loads(ledger_file.read_text())}
    count = json.loads(Path(stock_path or DATA / "stock_count.json").read_text())
    on_hand = {c["sku_id"]: c["on_hand"] for c in count["counts"]}
    return catalogue, suppliers, cards, ledger, on_hand, count["count_date"]


def lead_days(supplier):
    """Order in the morning: a 2-hour supplier restocks today, 12-24 hours means tomorrow."""
    return math.ceil(supplier["lead_time_hours"] / 24) if supplier["lead_time_hours"] > 4 else 0


def shelf_life(item):
    """Days a perishable can sit before spoiling (data/shelf_life.json); None = long-life."""
    return stocking_limit(item["sku_id"])


# --------------------------------------------------------------------------
# THE DECISION
# --------------------------------------------------------------------------
def price_option(item, card, supplier, rate, stock):
    """True cost of buying this SKU from this supplier today."""
    lt = lead_days(supplier)
    need = rate * (lt + TARGET_COVER_DAYS + SAFETY_DAYS) - stock
    life = shelf_life(item)
    old_left = max(0.0, stock - rate * lt)          # old stock still there when delivery lands
    if life is not None:
        need = min(need, rate * life - old_left)
    if need <= 0:
        return None
    upc = card["units_per_carton"]
    cartons = max(card["moq_cartons"], math.ceil(need / upc))
    pack, packs = upc, cartons
    too_much = rate and (stock + cartons * upc) / rate > MAX_STOCK_DAYS
    would_spoil = life is not None and cartons * upc > rate * life - old_left
    if too_much or would_spoil:
        if card["moq_cartons"] > 1:
            return None                      # bulk minimum is too much of this item
        pack = INNER_PACK                    # buy loose instead of a full carton
        packs = max(1, math.ceil(need / pack))
    qty = packs * pack
    purchase = qty * card["unit_cost"]
    unit_margin = item["selling_price"] - card["unit_cost"]

    # sales missed while waiting (plus the chance the delivery is a day late)
    short = max(0.0, rate * lt - stock) + (1 - supplier["reliability"]) * rate
    lost_margin = short * unit_margin

    # stock that will spoil before it can sell
    wasted = 0.0
    if life is not None:
        wasted = max(0.0, qty - (rate * life - old_left))
    usable = qty - wasted
    days_to_sell = usable / rate if rate else 999
    carrying = purchase * HOLD_COST_DAY * days_to_sell / 2
    credit = purchase * HOLD_COST_DAY * supplier["credit_days"]
    total = purchase + lost_margin + carrying + wasted * card["unit_cost"] - credit
    return {
        "supplier_id": supplier["supplier_id"],
        "packs": packs, "pack_size": pack, "qty": qty, "purchase": round(purchase, 2),
        "lost_margin": round(lost_margin, 2), "carrying": round(carrying, 2),
        "waste": round(wasted * card["unit_cost"], 2), "credit_value": round(credit, 2),
        "effective_unit_cost": round(total / usable, 3) if usable > 0 else float("inf"),
        "days_of_stock_bought": round(days_to_sell, 1),
    }


def needs_reorder(rate, stock, suppliers):
    normal_lead = min(lead_days(s) for s in suppliers.values() if lead_days(s) > 0)
    return rate > 0 and stock < rate * (normal_lead + REVIEW_DAYS + SAFETY_DAYS)


def plan(catalogue, suppliers, cards, rates, stock):
    """rates/stock: dict sku -> value. stock should include anything already on order."""
    lines = []
    for sku, item in catalogue.items():
        rate, have = rates.get(sku, 0.0), stock.get(sku, 0)
        if not needs_reorder(rate, have, suppliers):
            continue
        opts = [o for o in (price_option(item, cards[sku][sid], s, rate, have)
                            for sid, s in suppliers.items()) if o]
        if not opts:
            continue
        opts.sort(key=lambda o: o["effective_unit_cost"])
        best = opts[0]
        lines.append({
            "sku_id": sku, "item": item,
            "rate": rate, "on_hand": have,
            "days_left": round(have / rate, 1) if rate else None,
            "choice": best, "alternatives": opts[1:],
        })
    lines.sort(key=lambda l: l["days_left"] if l["days_left"] is not None else 99)
    return lines


# --------------------------------------------------------------------------
# OUTPUT FOR THE SHOPKEEPER
# --------------------------------------------------------------------------
def why(line, suppliers):
    c, alts = line["choice"], line["alternatives"]
    s = suppliers[c["supplier_id"]]
    bits = []
    if line["days_left"] is not None and line["days_left"] < 1:
        bits.append("almost out" if line["on_hand"] else "already out")
    if c["lost_margin"] and alts and s["lead_time_hours"] <= 4:
        bits.append(f'fast delivery ({s["lead_time_hours"]}h) saves missed sales')
    if s["credit_days"]:
        bits.append(f'{s["credit_days"]} days credit')
    if s["moq_cartons"] > 1:
        bits.append(f'bulk price, min {s["moq_cartons"]} cartons')
    if alts:
        a = alts[0]
        bits.append(f'Rs {a["effective_unit_cost"] - c["effective_unit_cost"]:.2f}/unit '
                    f'cheaper than {suppliers[a["supplier_id"]]["name"]}')
    return "; ".join(bits)


def purchase_orders(lines, suppliers, date):
    by_sup = defaultdict(list)
    for l in lines:
        by_sup[l["choice"]["supplier_id"]].append(l)
    msgs = []
    for sid, ls in sorted(by_sup.items()):
        s = suppliers[sid]
        total = sum(l["choice"]["purchase"] for l in ls)
        body = [f"Namaste {s['name']} ji, order for {date}:"]
        for i, l in enumerate(ls, 1):
            it = l["item"]
            c = l["choice"]
            n = c["packs"]
            unit = (("carton" if n == 1 else "cartons") if c["pack_size"] >= 12 else
                    ("pack" if n == 1 else "packs") + f" of {c['pack_size']}")
            body.append(f"{i}. {it['brand']} {it['product_name']} {it['pack_size']}"
                        f" x {n} {unit} ({c['qty']} pcs)")
        pay = (f"Payment in {s['credit_days']} days" if s["credit_days"]
               else f"Payment by UPI to {s['upi']} on delivery")
        body.append(f"Approx total Rs {total:,.0f}. {pay}. Thank you.")
        msgs.append({"supplier": s["name"], "phone": s["phone"], "lines": len(ls),
                     "total": round(total, 2), "message": "\n".join(body)})
    return msgs


# --------------------------------------------------------------------------
# SIMULATION  (evaluation only: reads the truth)
# --------------------------------------------------------------------------
def true_rates(days=7):
    txns = json.loads((DATA / "transactions.json").read_text())
    orders = json.loads((DATA / "ondc_orders.json").read_text())
    recent = set(sorted({t["timestamp"][:10] for t in txns})[-days:])
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


def poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def gut_feel_plan(catalogue, suppliers, cards, stock):
    usual = "SUP01"
    out = []
    for sku, item in catalogue.items():
        if stock.get(sku, 0) < 3:
            c = cards[sku][usual]
            out.append({"sku_id": sku, "item": item,
                        "choice": {"supplier_id": usual, "qty": c["units_per_carton"],
                                   "purchase": c["carton_price"]}})
    return out


def simulate(policy, catalogue, suppliers, cards, est_rates, truth, start_stock, seed,
             pos_rates=None):
    rng = random.Random(seed)
    stock = dict(start_stock)                  # real shelf
    belief = dict(start_stock)                 # what the app thinks is on the shelf
    pipeline = []                              # (arrive_day, sku, qty, lot_expiry_day)
    lots = defaultdict(list)                   # sku -> [[qty, expiry_day]] for perishables
    for sku, q in start_stock.items():
        life = shelf_life(catalogue[sku])
        lots[sku].append([q, (life or 999)])
    m = defaultdict(float)
    for day in range(SIM_DAYS):
        if day and day % RECOUNT_EVERY == 0:
            belief = dict(stock)
        on_order = defaultdict(int)
        for a, sku, q, _ in pipeline:
            on_order[sku] += q
        view = {s: belief.get(s, 0) + on_order[s] for s in catalogue}

        if policy == "gut":
            lines = gut_feel_plan(catalogue, suppliers, cards,
                                  {s: stock[s] + on_order[s] for s in catalogue})
        else:
            rates = {"shelfsense": est_rates, "pos": pos_rates}.get(policy, truth)
            lines = plan(catalogue, suppliers, cards, rates, view)
        for l in lines:
            s = suppliers[l["choice"]["supplier_id"]]
            lt = lead_days(s) + (1 if rng.random() > s["reliability"] else 0)
            life = shelf_life(l["item"])
            pipeline.append((day + lt, l["sku_id"], l["choice"]["qty"],
                             day + lt + (life or 999)))
            m["spend"] += l["choice"]["purchase"]
            m["orders"] += 1

        # deliveries
        keep = []
        for a, sku, q, exp in pipeline:
            if a <= day:
                stock[sku] += q
                belief[sku] = belief.get(sku, 0) + q
                lots[sku].append([q, exp])
            else:
                keep.append((a, sku, q, exp))
        pipeline = keep

        # demand
        for sku, item in catalogue.items():
            want = poisson(rng, truth.get(sku, 0.0))
            sold = min(want, stock[sku])
            stock[sku] -= sold
            belief[sku] = max(0.0, belief.get(sku, 0) - est_rates.get(sku, 0.0)
                              if policy == "shelfsense" else belief.get(sku, 0) - sold)
            margin = item["selling_price"] - cards[sku]["SUP01"]["unit_cost"]
            m["demand"] += want
            m["sold"] += sold
            m["lost_margin"] += (want - sold) * margin
            m["stockout_sku_days"] += 1 if want > sold else 0
            # use oldest lots first, then expire
            left = sold
            for lot in sorted(lots[sku], key=lambda x: x[1]):
                take = min(left, lot[0]); lot[0] -= take; left -= take
            for lot in lots[sku]:
                if lot[1] <= day and lot[0] > 0:
                    m["waste"] += lot[0] * cards[sku]["SUP01"]["unit_cost"]
                    stock[sku] -= lot[0]; lot[0] = 0
            lots[sku] = [l for l in lots[sku] if l[0] > 0]
            m["stock_value_days"] += stock[sku] * cards[sku]["SUP01"]["unit_cost"]
    m["avg_stock_value"] = m.pop("stock_value_days") / SIM_DAYS
    return m


def run_sims(catalogue, suppliers, cards, est_rates, truth, on_hand, pos_rates=None):
    out = {}
    policies = ["shelfsense"] + (["pos"] if pos_rates else []) + ["oracle", "gut"]
    for policy in policies:
        agg = defaultdict(float)
        for r in range(SIM_RUNS):
            for k, v in simulate(policy, catalogue, suppliers, cards, est_rates,
                                 truth, on_hand, seed=1000 + r, pos_rates=pos_rates).items():
                agg[k] += v / SIM_RUNS
        agg["fill_rate_pct"] = 100.0 * agg["sold"] / agg["demand"]
        out[policy] = dict(agg)
    return out


def save_outputs(lines, pos, suppliers, date):
    rows = [{
        "sku_id": l["sku_id"],
        "name": f"{l['item']['brand']} {l['item']['product_name']} {l['item']['pack_size']}",
        "on_hand": l["on_hand"], "est_daily_rate": l["rate"], "days_left": l["days_left"],
        "buy_from": suppliers[l["choice"]["supplier_id"]]["name"],
        "packs": l["choice"]["packs"], "pack_size": l["choice"]["pack_size"], "qty": l["choice"]["qty"],
        "cost": l["choice"]["purchase"],
        "effective_unit_cost": l["choice"]["effective_unit_cost"],
        "why": why(l, suppliers),
        "alternatives": [{"supplier": suppliers[a["supplier_id"]]["name"],
                          "effective_unit_cost": a["effective_unit_cost"],
                          "qty": a["qty"]} for a in l["alternatives"]],
    } for l in lines]
    (OUT / "reorder_today.json").write_text(json.dumps(rows, indent=2))
    (OUT / "purchase_orders.json").write_text(json.dumps(pos, indent=2))
    # keep every day's order, so delivery bills can be checked against the right one
    (OUT / "orders").mkdir(exist_ok=True)
    (OUT / "orders" / f"orders_{date}.json").write_text(json.dumps(
        {"date": date, "lines": rows}, indent=2))


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="What to reorder today, and from whom.")
    ap.add_argument("--stock", help="stock file to plan from (default data/stock_count.json; "
                                    "after deliveries use out/stock_now.json)")
    ap.add_argument("--sales", choices=["estimate", "billing_station"], default="estimate",
                    help="where daily sales numbers come from (billing_station needs step 5 data)")
    ap.add_argument("--no-test", action="store_true", help="skip the 14-day simulation")
    args = ap.parse_args()
    catalogue, suppliers, cards, ledger, on_hand, date = load(args.stock, args.sales)
    est = {s: r["est_daily_rate_recent"] for s, r in ledger.items()}
    pos_file = OUT / "ledger_pos.json"
    pos_rates = ({r["sku_id"]: r["est_daily_rate_recent"] for r in json.loads(pos_file.read_text())}
                 if pos_file.exists() else None)

    lines = plan(catalogue, suppliers, cards, est, on_hand)
    pos = purchase_orders(lines, suppliers, date)

    print("=" * 76)
    print(f"ShelfSense — REORDER LIST FOR {date}")
    print(f"stock from {args.stock or 'data/stock_count.json'}; sales numbers: {args.sales}")
    print("=" * 76)
    print(f"{len(lines)} of {len(catalogue)} items need ordering today\n")
    print(f"{'item':<32}{'shelf':>6}{'/day':>6}{'days':>6}  {'buy from':<20}{'qty':>5}{'Rs':>8}")
    for l in lines:
        it, c = l["item"], l["choice"]
        print(f"{(it['brand'] + ' ' + it['product_name'] + ' ' + it['pack_size'])[:31]:<32}"
              f"{l['on_hand']:>6}{l['rate']:>6.1f}{l['days_left']:>6.1f}  "
              f"{suppliers[c['supplier_id']]['name']:<20}{c['qty']:>5}{c['purchase']:>8,.0f}")
    print("\nPURCHASE ORDERS (ready to send on WhatsApp)")
    for p in pos:
        print(f"\n--- {p['supplier']}  ({p['lines']} items, Rs {p['total']:,.0f}) ---")
        print(p["message"])

    OUT.mkdir(exist_ok=True)
    save_outputs(lines, pos, suppliers, date)
    if args.no_test:
        return
    base_est = {s: r["est_daily_rate_recent"]
                for s, r in ((r["sku_id"], r) for r in json.loads((OUT / "ledger.json").read_text()))}
    truth = true_rates()
    sims = run_sims(catalogue, suppliers, cards, base_est, truth, on_hand, pos_rates)
    print("\n" + "=" * 76)
    print(f"TEST: {SIM_DAYS}-day shop simulation x {SIM_RUNS} runs (averages)")
    print("=" * 76)
    names = {"shelfsense": "ShelfSense", "pos": "+ billing station",
             "oracle": "Perfect knowledge", "gut": "Gut feel"}
    print(f"{'':<20}{'fill rate':>10}{'out-of-stock':>14}{'lost profit':>13}"
          f"{'stock held':>12}{'spoiled':>9}{'orders':>8}")
    print(f"{'':<20}{'':>10}{'item-days':>14}{'Rs':>13}{'Rs (avg)':>12}{'Rs':>9}{'':>8}")
    for k, v in sims.items():
        print(f"{names[k]:<20}{v['fill_rate_pct']:>9.1f}%{v['stockout_sku_days']:>14.0f}"
              f"{v['lost_margin']:>13,.0f}{v['avg_stock_value']:>12,.0f}"
              f"{v['waste']:>9,.0f}{v['orders']:>8.0f}")
    daily_cost = sum(truth.get(k, 0) * cards[k]["SUP01"]["unit_cost"] for k in catalogue)
    print("\nfill rate  = share of customers who found the item on the shelf")
    print("stock held = money sitting on shelves; "
          + ", ".join(f"{names[k]} ~{v['avg_stock_value'] / daily_cost:.0f} days of sales"
                      for k, v in sims.items()))
    for v in sims.values():
        v["stock_days_of_sales"] = v["avg_stock_value"] / daily_cost
    print("=" * 76)

    (OUT / "reorder_result.json").write_text(json.dumps({
        "date": date, "items_to_order": len(lines), "sales_source": args.sales,
        "spend_today": round(sum(p["total"] for p in pos), 2),
        "simulation": {names[k]: {kk: round(vv, 1) for kk, vv in v.items()}
                       for k, v in sims.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
