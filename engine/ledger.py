"""
ShelfSense — step 2: the fallback inventory ledger + its accuracy test.

WHY THIS EXISTS
    The gate test said FALLBACK: per-payment guessing is right ~31% of the time,
    so a single Soundbox payment cannot be trusted to say what was sold.
    The fallback plan:
      * ONDC orders are the TRUTH layer  -> itemised, so units are exact.
      * Soundbox payments are a CONFIDENCE signal -> used only in aggregate,
        as an estimate with an uncertainty band, never as a hard stock movement.

THE IDEA THAT MAKES SOUNDBOX STILL USEFUL
    Being wrong on each payment is not the same as being wrong in total.
    Instead of trusting the engine's single top guess, we spread each payment
    across ALL candidate baskets in proportion to the engine's confidence
    ("expected units"). Over hundreds of payments many errors cancel out.
    This file measures whether that is actually true.

WHAT IT PRODUCES
    out/ledger.json         per-SKU: confirmed units (ONDC), estimated walk-in
                            units (Soundbox), a low/high band, daily rate
    out/ledger_result.json  headline accuracy numbers for the pitch

WHAT IT MEASURES (all against ground truth, which the ledger never reads)
    WAPE = total absolute error / total true units. Lower is better. 0% is perfect.
    Measured per SKU over 30 days, and per SKU per day (what reordering needs).
"""

import json, math, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from disambiguate import DisambiguationEngine  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / "data", ROOT / "out"

# Share of walk-in sales paid in cash (never seen by the Soundbox). In a real shop
# this is the shopkeeper's estimate; here we test what happens if it's wrong.
ASSUMED_CASH_SHARE = 0.18
Z = 1.645                               # 90% band

# Which walk-in estimate feeds out/ledger.json (and so the reorder step).
#   "popularity" : split each day's Soundbox takings by catalogue popularity.
#                  Most accurate on the synthetic data (chosen 2026-09-17), but it
#                  cannot notice one product selling faster or slower than usual.
#   "expected"   : spread each payment over its candidate baskets (per-payment signal).
ESTIMATOR = "popularity"
RECENT_DAYS = 7                         # window for the "recent" daily rate


# --------------------------------------------------------------------------
# ESTIMATORS  (none of these may read truth_* fields)
# --------------------------------------------------------------------------
def soundbox_posterior(engine, txns, cash_share):
    """Expected walk-in units per (sku, day), plus variance, from ALL candidate baskets."""
    mean = defaultdict(float)
    var = defaultdict(float)
    scale = 1.0 / (1.0 - cash_share)
    for t in txns:
        if not t["visible_to_engine"]:
            continue
        day, hour = t["timestamp"][:10], int(t["timestamp"][11:13])
        ranked = engine.rank(t["amount"], hour, top_n=10**6)
        e1, e2 = defaultdict(float), defaultdict(float)     # E[count], E[count^2]
        for r in ranked:
            counts = defaultdict(int)
            for s in r["basket"]:
                counts[s] += 1
            for s, c in counts.items():
                e1[s] += r["confidence"] * c
                e2[s] += r["confidence"] * c * c
        for s in e1:
            mean[(s, day)] += e1[s] * scale
            var[(s, day)] += (e2[s] - e1[s] ** 2) * scale ** 2
    return mean, var


def soundbox_top1(engine, txns, cash_share):
    """What the ORIGINAL plan would do: trust the single best guess per payment."""
    mean = defaultdict(float)
    scale = 1.0 / (1.0 - cash_share)
    for t in txns:
        if not t["visible_to_engine"]:
            continue
        r = engine.rank(t["amount"], int(t["timestamp"][11:13]), top_n=1)
        if r:
            for s in r[0]["basket"]:
                mean[(s, t["timestamp"][:10])] += scale
    return mean


def popularity_split(catalogue, txns, cash_share):
    """Naive baseline: split each day's Soundbox revenue across SKUs by catalogue popularity."""
    w = {c["sku_id"]: c["base_daily_units"] for c in catalogue}
    price = {c["sku_id"]: c["selling_price"] for c in catalogue}
    avg_price = sum(w[s] * price[s] for s in w) / sum(w.values())
    rev = defaultdict(float)
    for t in txns:
        if t["visible_to_engine"]:
            rev[t["timestamp"][:10]] += t["amount"]
    tot_w = sum(w.values())
    mean = {}
    for day, r in rev.items():
        units = r / (1.0 - cash_share) / avg_price
        for s in w:
            mean[(s, day)] = units * w[s] / tot_w
    return mean


POS_FILES = (DATA / "events" / "pos_sales.json", DATA / "events" / "screen_scans.json")


def pos_walkin():
    """Walk-in sales from a billing/scanning station (step 5), or None.
    Reads the billing-software feed and the phone-screen scanner. Items billed
    without a barcode ("LOOSE ITEM") are spread across that day's scanned items
    in proportion, since their count is known but not which product they were."""
    out = defaultdict(float)
    for path in POS_FILES:
        if not path.exists():
            continue
        s = json.loads(path.read_text())
        for day, units in s.get("days", {}).items():
            matched = sum(units.values())
            miss = sum(s.get("unmatched", {}).get(day, {}).values())
            scale = (matched + miss) / matched if matched else 1.0
            for sku, q in units.items():
                out[(sku, day)] += q * scale
    return dict(out) or None


def ondc_units(orders):
    u = defaultdict(float)
    for o in orders:
        if o["status"] != "DELIVERED":
            continue
        for it in o["items"]:
            u[(it["sku_id"], o["timestamp"][:10])] += it["qty"]
    return u


# --------------------------------------------------------------------------
# GROUND TRUTH  (evaluation only)
# --------------------------------------------------------------------------
def walkin_truth(txns):
    u = defaultdict(float)
    for t in txns:                       # includes cash sales: they empty shelves too
        for s, q in t["truth_basket"].items():
            u[(s, t["timestamp"][:10])] += q
    return u


# --------------------------------------------------------------------------
# SCORING
# --------------------------------------------------------------------------
def wape(est, truth, keys):
    err = sum(abs(est.get(k, 0.0) - truth.get(k, 0.0)) for k in keys)
    tot = sum(truth.get(k, 0.0) for k in keys)
    return 100.0 * err / tot if tot else float("nan")


def roll_up(d):
    r = defaultdict(float)
    for (s, _), v in d.items():
        r[s] += v
    return r


def pct(x):
    return f"{x:5.1f}%"


def main():
    catalogue = json.loads((DATA / "catalogue.json").read_text())
    txns = json.loads((DATA / "transactions.json").read_text())
    orders = json.loads((DATA / "ondc_orders.json").read_text())
    engine = DisambiguationEngine(catalogue)

    skus = [c["sku_id"] for c in catalogue]
    days = sorted({t["timestamp"][:10] for t in txns})
    sku_day = [(s, d) for s in skus for d in days]

    ondc = ondc_units(orders)
    truth_walk = walkin_truth(txns)
    truth_total = defaultdict(float)
    for k in set(truth_walk) | set(ondc):
        truth_total[k] = truth_walk.get(k, 0.0) + ondc.get(k, 0.0)

    post_mean, post_var = soundbox_posterior(engine, txns, ASSUMED_CASH_SHARE)
    top1 = soundbox_top1(engine, txns, ASSUMED_CASH_SHARE)
    pop = popularity_split(catalogue, txns, ASSUMED_CASH_SHARE)

    def total_with(walk_est):
        return {k: ondc.get(k, 0.0) + walk_est.get(k, 0.0) for k in sku_day}

    methods = {
        "ONDC only (ignore Soundbox)": {k: ondc.get(k, 0.0) for k in sku_day},
        "ONDC + Soundbox top-1 guess (original plan)": total_with(top1),
        "ONDC + Soundbox expected units (fallback)": total_with(post_mean),
        "ONDC + popularity split (naive baseline)": total_with(pop),
    }
    pos = pos_walkin()
    if pos:
        methods["ONDC + billing station (shops with a scanner)"] = total_with(pos)

    truth_sku = roll_up({k: truth_total.get(k, 0.0) for k in sku_day})
    total_true_units = sum(truth_sku.values())
    ondc_share = 100.0 * sum(ondc.values()) / total_true_units

    print("=" * 72)
    print("ShelfSense — STEP 2: FALLBACK INVENTORY LEDGER")
    print("=" * 72)
    print(f"true units sold in 30 days : {total_true_units:,.0f}")
    print(f"  of which ONDC (exact)    : {sum(ondc.values()):,.0f}  ({ondc_share:.1f}%)")
    print(f"assumed cash share         : {ASSUMED_CASH_SHARE:.0%}")
    print("\nError in units-sold estimate (WAPE, lower is better)")
    print(f"  {'method':<48}{'per SKU, 30d':>13}{'per SKU, day':>14}")
    results = {}
    for name, est in methods.items():
        a = wape(roll_up(est), truth_sku, skus)
        b = wape(est, truth_total, sku_day)
        results[name] = (a, b)
        print(f"  {name:<48}{pct(a):>13}{pct(b):>14}")

    # ---- does the uncertainty band tell the truth? ----
    if ESTIMATOR == "popularity":
        walk = pop
        # the split itself has no spread; treat units as Poisson-like counts
        walk_var = {k: v for k, v in pop.items()}
    else:
        walk, walk_var = post_mean, post_var
    def build(walk, walk_var, source):
        sku_mean, sku_var = roll_up(walk), roll_up(walk_var)
        recent = set(days[-RECENT_DAYS:])
        recent_units = defaultdict(float)
        for (s_, d_), v in list(walk.items()) + list(ondc.items()):
            if d_ in recent:
                recent_units[s_] += v
        ondc_sku = roll_up(ondc)
        inside = 0
        ledger = []
        by_sku = {c["sku_id"]: c for c in catalogue}
        for s in skus:
            m, sd = sku_mean.get(s, 0.0), math.sqrt(sku_var.get(s, 0.0))
            lo, hi = ondc_sku.get(s, 0.0) + max(0.0, m - Z * sd), ondc_sku.get(s, 0.0) + m + Z * sd
            inside += lo <= truth_sku[s] <= hi
            c = by_sku[s]
            ledger.append({
                "sku_id": s,
                "name": f'{c["brand"]} {c["product_name"]} {c["pack_size"]}',
                "confirmed_units_ondc": round(ondc_sku.get(s, 0.0), 1),
                "estimated_walkin_units": round(m, 1),
                "estimated_total_units": round(ondc_sku.get(s, 0.0) + m, 1),
                "band_90": [round(lo, 1), round(hi, 1)],
                "est_daily_rate": round((ondc_sku.get(s, 0.0) + m) / len(days), 2),
                "est_daily_rate_recent": round(recent_units[s] / RECENT_DAYS, 2),
                "sales_source": source,
                "signal_quality": ("scanned" if source == "billing_station" else
                                   "confirmed" if ondc_sku.get(s, 0.0) >= m else
                                   "estimated-tight" if sd <= 0.15 * max(m, 1) else
                                   "estimated-loose"),
            })
        coverage = 100.0 * inside / len(skus)
        return ledger, coverage

    ledger, coverage = build(walk, walk_var, "estimate")
    print(f"\nledger.json uses the '{ESTIMATOR}' walk-in estimate")
    print(f"90% band actually contains the truth for {coverage:.0f}% of SKUs "
          f"(should be ~90%; lower means the band is over-confident)")

    # ---- what if the shopkeeper's cash guess is wrong? ----
    print(f"\nSensitivity ({ESTIMATOR}): wrong cash-share assumption (true share is ~18%)")
    sens = {}
    for cs in (0.05, 0.18, 0.30):
        if ESTIMATOR == "popularity":
            pm = popularity_split(catalogue, txns, cs)
        else:
            pm, _ = soundbox_posterior(engine, txns, cs)
        a = wape(roll_up(total_with(pm)), truth_sku, skus)
        sens[cs] = a
        print(f"  assume {cs:.0%} cash -> per-SKU 30d WAPE {pct(a)}")

    # ---- where does it still break? ----
    print("\nWorst 5 SKUs (30-day estimate vs truth):")
    worst = sorted(ledger, key=lambda r: -abs(r["estimated_total_units"] - truth_sku[r["sku_id"]]))[:5]
    for r in worst:
        print(f"  {r['name'][:34]:<34} est {r['estimated_total_units']:7.1f}  "
              f"true {truth_sku[r['sku_id']]:6.0f}")

    fb = results["ONDC + Soundbox expected units (fallback)"]
    t1 = results["ONDC + Soundbox top-1 guess (original plan)"]
    pp = results["ONDC + popularity split (naive baseline)"]
    print("\n" + "=" * 72)
    print(f"FALLBACK LEDGER 30-day error: {fb[0]:.1f}%   (top-1 plan: {t1[0]:.1f}%)")
    print(f"NAIVE BASELINE  30-day error: {pp[0]:.1f}%")
    if pp[0] <= fb[0]:
        print("WARNING: the naive popularity split is as good or better. On this\n"
              "         synthetic data the engine's prior IS the generator's recipe,\n"
              "         so this test cannot prove Soundbox adds value. Real shop data\n"
              "         is needed before claiming it does.")
    print("=" * 72)

    OUT.mkdir(exist_ok=True)
    pos_cov = None
    if pos:
        # a scanner misses little: ~3% loose items, spread in proportion
        pos_led, pos_cov = build(pos, {k: (0.03 * v) ** 2 + 0.25 for k, v in pos.items()},
                                 "billing_station")
        pos_led.sort(key=lambda r: -r["estimated_total_units"])
        (OUT / "ledger_pos.json").write_text(json.dumps(pos_led, indent=2))
        pw = results["ONDC + billing station (shops with a scanner)"]
        print(f"BILLING STATION 30-day error: {pw[0]:.1f}%  (per SKU per day {pw[1]:.1f}%) "
              f"-> out/ledger_pos.json, band holds truth for {pos_cov:.0f}% of SKUs")
    ledger.sort(key=lambda r: -r["estimated_total_units"])
    (OUT / "ledger.json").write_text(json.dumps(ledger, indent=2))
    (OUT / "ledger_result.json").write_text(json.dumps({
        "ondc_share_of_units_pct": round(ondc_share, 1),
        "wape_sku_30d": {k: round(v[0], 2) for k, v in results.items()},
        "wape_sku_day": {k: round(v[1], 2) for k, v in results.items()},
        "ledger_estimator": ESTIMATOR,
        "band90_coverage_pct": round(coverage, 1),
        "cash_share_sensitivity_wape_sku_30d": {f"{k:.2f}": round(v, 2) for k, v in sens.items()},
        "fallback_beats_naive": fb[0] < pp[0],
        "billing_station_band90_coverage_pct": round(pos_cov, 1) if pos_cov is not None else None,
    }, indent=2))


if __name__ == "__main__":
    main()
