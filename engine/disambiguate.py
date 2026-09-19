"""
ShelfSense — SKU disambiguation engine + accuracy harness.

THE PROBLEM
    A Paytm Soundbox emits (amount, timestamp, payer VPA). It does NOT emit
    what was sold. To treat payments as an inventory signal we must infer the
    basket from the amount alone.

THE APPROACH
    1. Enumerate every basket of 1-3 SKUs whose total equals the amount.
    2. Score each candidate by a prior: SKU popularity x hour-of-day propensity
       x a penalty for larger baskets (most kirana payments are single-item).
    3. Return a ranked list. Top-1 is the engine's answer.

WHAT WE MEASURE
    primary-SKU top-1 : did we identify the basket's highest-value SKU?
    primary-SKU top-3 : was it in our top three guesses?
    exact-basket top-1 : did we get the entire basket right?
    Reported overall AND split by basket size and by price ambiguity, because
    the aggregate number hides where this actually breaks.

BASELINE
    "Most popular SKU at that exact price." Any learned scoring must beat this
    or it is not earning its place in the pitch.
"""

import json, itertools, statistics
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

MAX_BASKET = 3
BASKET_SIZE_PENALTY = {1: 1.00, 2: 0.34, 3: 0.11}   # priors on basket size

# Category propensity by hour band. Morning = staples/dairy; evening = snacks.
HOUR_BANDS = {
    "morning": range(6, 12),
    "midday":  range(12, 16),
    "evening": range(16, 23),
}
CATEGORY_HOUR_LIFT = {
    "Dairy":      {"morning": 1.9, "midday": 0.7, "evening": 0.9},
    "Staples":    {"morning": 1.4, "midday": 1.0, "evening": 0.8},
    "Beverages":  {"morning": 0.8, "midday": 1.2, "evening": 1.4},
    "Snacks":     {"morning": 0.6, "midday": 1.1, "evening": 1.7},
    "Confection": {"morning": 0.6, "midday": 1.1, "evening": 1.6},
    "Biscuits":   {"morning": 1.1, "midday": 1.2, "evening": 1.2},
    "Noodles":    {"morning": 0.7, "midday": 1.2, "evening": 1.4},
    "Personal":   {"morning": 1.0, "midday": 1.0, "evening": 1.0},
    "Home Care":  {"morning": 1.0, "midday": 1.1, "evening": 0.9},
    "Oil":        {"morning": 1.2, "midday": 1.0, "evening": 0.9},
    "Spices":     {"morning": 1.1, "midday": 1.0, "evening": 0.9},
    "Spreads":    {"morning": 1.2, "midday": 0.9, "evening": 0.9},
}


def band_for_hour(h):
    for name, rng in HOUR_BANDS.items():
        if h in rng:
            return name
    return "evening"


class DisambiguationEngine:
    def __init__(self, catalogue):
        self.catalogue = catalogue
        self.by_sku = {c["sku_id"]: c for c in catalogue}
        total = sum(c["base_daily_units"] for c in catalogue)
        self.popularity = {c["sku_id"]: c["base_daily_units"] / total for c in catalogue}

        # index price -> SKUs, and precompute reachable sums
        self.price_index = defaultdict(list)
        for c in catalogue:
            self.price_index[int(c["selling_price"])].append(c["sku_id"])

        self._combo_index = self._build_combo_index()

    def _build_combo_index(self):
        """amount -> list of candidate baskets (tuples of sku_ids)."""
        idx = defaultdict(list)
        prices = sorted(self.price_index.keys())

        # size 1
        for p in prices:
            for sid in self.price_index[p]:
                idx[p].append((sid,))
        # size 2 (unordered, allows same SKU twice)
        for i, p1 in enumerate(prices):
            for p2 in prices[i:]:
                tot = p1 + p2
                for s1 in self.price_index[p1]:
                    for s2 in self.price_index[p2]:
                        if s1 <= s2:
                            idx[tot].append((s1, s2))
        # size 3
        for i, p1 in enumerate(prices):
            for j in range(i, len(prices)):
                p2 = prices[j]
                for k in range(j, len(prices)):
                    p3 = prices[k]
                    tot = p1 + p2 + p3
                    for s1 in self.price_index[p1]:
                        for s2 in self.price_index[p2]:
                            if s2 < s1:
                                continue
                            for s3 in self.price_index[p3]:
                                if s3 < s2:
                                    continue
                                idx[tot].append((s1, s2, s3))
        return idx

    def score(self, basket, hour):
        band = band_for_hour(hour)
        s = BASKET_SIZE_PENALTY.get(len(basket), 0.05)
        for sid in basket:
            item = self.by_sku[sid]
            lift = CATEGORY_HOUR_LIFT.get(item["category"], {}).get(band, 1.0)
            s *= self.popularity[sid] * lift
        return s

    def rank(self, amount, hour, top_n=5):
        cands = self._combo_index.get(int(round(amount)), [])
        if not cands:
            return []
        scored = [(self.score(b, hour), b) for b in cands]
        scored.sort(key=lambda x: -x[0])
        tot = sum(s for s, _ in scored) or 1.0
        return [
            {
                "basket": list(b),
                "primary_sku": max(b, key=lambda s: self.by_sku[s]["selling_price"]),
                "confidence": round(s / tot, 4),
            }
            for s, b in scored[:top_n]
        ]


class PopularityBaseline:
    """Naive: the single most popular SKU whose price equals the amount."""
    def __init__(self, catalogue):
        self.by_sku = {c["sku_id"]: c for c in catalogue}
        self.price_index = defaultdict(list)
        for c in catalogue:
            self.price_index[int(c["selling_price"])].append(c["sku_id"])
        for p in self.price_index:
            self.price_index[p].sort(
                key=lambda s: -self.by_sku[s]["base_daily_units"])

    def rank(self, amount, hour, top_n=5):
        cands = self.price_index.get(int(round(amount)), [])
        return [{"basket": [s], "primary_sku": s, "confidence": 0.0}
                for s in cands[:top_n]]


# --------------------------------------------------------------------------
# HARNESS
# --------------------------------------------------------------------------
def evaluate(engine, txns, label):
    res = {
        "label": label,
        "n": 0,
        "p1": 0, "p3": 0, "exact": 0, "no_candidate": 0,
        "by_size": defaultdict(lambda: {"n": 0, "p1": 0}),
        "by_ambiguity": defaultdict(lambda: {"n": 0, "p1": 0}),
    }
    for t in txns:
        if not t["visible_to_engine"]:
            continue
        hour = int(t["timestamp"][11:13])
        ranked = engine.rank(t["amount"], hour, top_n=3)
        res["n"] += 1
        size = sum(t["truth_basket"].values())
        res["by_size"][size]["n"] += 1

        n_cand = len(engine.rank(t["amount"], hour, top_n=999))
        bucket = ("unique" if n_cand <= 1 else
                  "2-5" if n_cand <= 5 else
                  "6-20" if n_cand <= 20 else "20+")
        res["by_ambiguity"][bucket]["n"] += 1

        if not ranked:
            res["no_candidate"] += 1
            continue

        truth_primary = t["truth_primary_sku"]
        truth_basket = sorted(
            [s for s, q in t["truth_basket"].items() for _ in range(q)])

        if ranked[0]["primary_sku"] == truth_primary:
            res["p1"] += 1
            res["by_size"][size]["p1"] += 1
            res["by_ambiguity"][bucket]["p1"] += 1
        if any(r["primary_sku"] == truth_primary for r in ranked):
            res["p3"] += 1
        if sorted(ranked[0]["basket"]) == truth_basket:
            res["exact"] += 1
    return res


def pct(a, b):
    return f"{(100.0 * a / b):5.1f}%" if b else "  n/a"


def report(res):
    n = res["n"]
    print(f"\n--- {res['label']}  (n={n}) ---")
    print(f"  primary-SKU top-1 : {pct(res['p1'], n)}")
    print(f"  primary-SKU top-3 : {pct(res['p3'], n)}")
    print(f"  exact basket top-1: {pct(res['exact'], n)}")
    print(f"  no candidate found: {pct(res['no_candidate'], n)}")
    print("  by basket size:")
    for size in sorted(res["by_size"]):
        d = res["by_size"][size]
        print(f"     {size} item(s): {pct(d['p1'], d['n'])}  (n={d['n']})")
    print("  by price ambiguity (candidate baskets at that amount):")
    for b in ["unique", "2-5", "6-20", "20+"]:
        d = res["by_ambiguity"].get(b)
        if d and d["n"]:
            print(f"     {b:>6}: {pct(d['p1'], d['n'])}  (n={d['n']})")


def coverage_curve(engine, txns):
    """If we only act when confident, how good are we — and how often can we act?"""
    rows = []
    for t in txns:
        if not t["visible_to_engine"]:
            continue
        hour = int(t["timestamp"][11:13])
        r = engine.rank(t["amount"], hour, top_n=1)
        if not r:
            continue
        rows.append((r[0]["confidence"], r[0]["primary_sku"] == t["truth_primary_sku"]))
    rows.sort(key=lambda x: -x[0])
    n = len(rows)
    print("\n--- PRECISION AT COVERAGE (act only on most-confident calls) ---")
    print("  coverage   threshold   precision")
    for cov in [0.05, 0.10, 0.20, 0.30, 0.50, 1.00]:
        k = max(1, int(n * cov))
        sub = rows[:k]
        prec = 100.0 * sum(1 for _, ok in sub if ok) / k
        print(f"   {cov*100:5.0f}%      {sub[-1][0]:.3f}      {prec:5.1f}%")
    return rows


def main():
    catalogue = json.loads((DATA / "catalogue.json").read_text())
    txns = json.loads((DATA / "transactions.json").read_text())

    print("=" * 66)
    print("ShelfSense — SKU DISAMBIGUATION GATE TEST")
    print("=" * 66)
    print(f"catalogue: {len(catalogue)} SKUs   transactions: {len(txns)}")

    base = PopularityBaseline(catalogue)
    eng = DisambiguationEngine(catalogue)

    r_base = evaluate(base, txns, "BASELINE  (most popular SKU at price)")
    r_eng = evaluate(eng, txns, "ENGINE    (basket enumeration + time-of-day prior)")

    report(r_base)
    report(r_eng)

    coverage_curve(eng, txns)

    lift = (100.0 * r_eng["p1"] / r_eng["n"]) - (100.0 * r_base["p1"] / r_base["n"])
    print("\n" + "=" * 66)
    print(f"TOP-1 LIFT OVER BASELINE: {lift:+.1f} percentage points")
    acc = 100.0 * r_eng["p1"] / r_eng["n"]
    print(f"ENGINE TOP-1 ACCURACY   : {acc:.1f}%  (n={r_eng['n']})")
    print("GATE DECISION           : ", end="")
    if acc >= 70:
        print("GO — Soundbox inference is the inventory signal.")
    else:
        print("FALL BACK — make ONDC orders the inventory truth;\n"
              "                          demote Soundbox to a confidence signal.")
    print("=" * 66)

    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "gate_result.json").write_text(json.dumps({
        "engine_top1": round(acc, 2),
        "baseline_top1": round(100.0 * r_base["p1"] / r_base["n"], 2),
        "engine_top3": round(100.0 * r_eng["p3"] / r_eng["n"], 2),
        "exact_basket_top1": round(100.0 * r_eng["exact"] / r_eng["n"], 2),
        "n_evaluated": r_eng["n"],
        "decision": "GO" if acc >= 70 else "FALLBACK",
    }, indent=2))


if __name__ == "__main__":
    main()
