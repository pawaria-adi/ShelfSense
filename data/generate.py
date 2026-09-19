"""
ShelfSense — synthetic fixture generator.

Builds three artefacts:
  1. catalogue.json       100 Indian FMCG SKUs with realistic MRPs (deliberate price collisions)
  2. suppliers.json       3 distributors with differing price / MOQ / credit / lead time
  3. transactions.json    30 days of Soundbox-style payments WITH ground-truth baskets

Ground truth is the whole point: every transaction records exactly which SKUs were
in the basket, so the disambiguation engine can be scored honestly against it.
"""

import json, random, math
from datetime import datetime, timedelta
from pathlib import Path

RNG = random.Random(20260917)
OUT = Path(__file__).resolve().parent

# --------------------------------------------------------------------------
# 1. CATALOGUE
# --------------------------------------------------------------------------
# Realistic Indian kirana FMCG. Prices chosen to create genuine collisions —
# several distinct SKUs share the same price point, which is the hard case.

CATALOGUE_SEED = [
    # (brand, product, pack, mrp, category, hsn, gst, base_daily_units)
    ("Parle",      "Parle-G Biscuits",       "70g",    10,  "Biscuits",   "1905", 18, 42),
    ("Britannia",  "Good Day Cashew",        "75g",    10,  "Biscuits",   "1905", 18, 30),
    ("Britannia",  "Bourbon",                "60g",    10,  "Biscuits",   "1905", 18, 22),
    ("Sunfeast",   "Marie Light",            "75g",    10,  "Biscuits",   "1905", 18, 18),
    ("Parle",      "Krackjack",              "70g",    10,  "Biscuits",   "1905", 18, 15),
    ("Lay's",      "Classic Salted",         "52g",    20,  "Snacks",     "2005", 12, 34),
    ("Kurkure",    "Masala Munch",           "90g",    20,  "Snacks",     "2005", 12, 31),
    ("Bingo",      "Mad Angles",             "66g",    20,  "Snacks",     "2005", 12, 19),
    ("Haldiram",   "Aloo Bhujia",            "42g",    20,  "Snacks",     "2005", 12, 17),
    ("Maggi",      "Masala Noodles",         "70g",    14,  "Noodles",    "1902",  18, 46),
    ("Maggi",      "Masala Noodles 4-pack",  "280g",   56,  "Noodles",    "1902",  18, 12),
    ("Yippee",     "Magic Masala",           "70g",    14,  "Noodles",    "1902",  18, 16),
    ("Top Ramen",  "Curry Noodles",          "70g",    14,  "Noodles",    "1902",  18,  9),
    ("Amul",       "Taaza Milk",             "500ml",  27,  "Dairy",      "0401",  0,  68),
    ("Amul",       "Gold Milk",              "500ml",  33,  "Dairy",      "0401",  0,  41),
    ("Mother Dairy","Toned Milk",            "500ml",  27,  "Dairy",      "0401",  0,  29),
    ("Amul",       "Butter",                 "100g",   62,  "Dairy",      "0405",  12, 14),
    ("Amul",       "Butter",                 "500g",   295, "Dairy",      "0405",  12,  4),
    ("Amul",       "Cheese Slices",          "200g",   145, "Dairy",      "0406",  12,  5),
    ("Amul",       "Masti Dahi",             "400g",   45,  "Dairy",      "0403",  5,  16),
    ("Nestle",     "Milkmaid",               "400g",   145, "Dairy",      "0402",  12,  3),
    ("Fortune",    "Sunflower Oil",          "1L",     135, "Oil",        "1512",  5,  11),
    ("Fortune",    "Soyabean Oil",           "1L",     125, "Oil",        "1507",  5,   8),
    ("Saffola",    "Gold Oil",               "1L",     175, "Oil",        "1512",  5,   6),
    ("Dhara",      "Mustard Oil",            "1L",     155, "Oil",        "1514",  5,   7),
    ("Fortune",    "Sunflower Oil",          "5L",     640, "Oil",        "1512",  5,   2),
    ("Tata",       "Salt",                   "1kg",    28,  "Staples",    "2501",  0,  22),
    ("Tata",       "Sampann Toor Dal",       "1kg",    165, "Staples",    "0713",  0,   6),
    ("Aashirvaad", "Atta",                   "5kg",    285, "Staples",    "1101",  0,   9),
    ("Aashirvaad", "Atta",                   "1kg",    62,  "Staples",    "1101",  0,  13),
    ("India Gate", "Basmati Rice",           "1kg",    135, "Staples",    "1006",  0,   7),
    ("Daawat",     "Rozana Rice",            "5kg",    340, "Staples",    "1006",  0,   3),
    ("Madhur",     "Sugar",                  "1kg",    48,  "Staples",    "1701",  0,  18),
    ("Tata",       "Tea Gold",               "250g",   145, "Beverages",  "0902",  5,  12),
    ("Red Label",  "Tea",                    "250g",   135, "Beverages",  "0902",  5,  14),
    ("Taj Mahal",  "Tea",                    "250g",   175, "Beverages",  "0902",  5,   6),
    ("Nescafe",    "Classic Coffee",         "50g",    175, "Beverages",  "0901",  18,  5),
    ("Bru",        "Instant Coffee",         "50g",    155, "Beverages",  "0901",  18,  4),
    ("Bournvita",  "Health Drink",           "500g",   245, "Beverages",  "1806",  18,  4),
    ("Horlicks",   "Classic Malt",           "500g",   255, "Beverages",  "1901",  18,  3),
    ("Coca-Cola",  "Soft Drink",             "750ml",  40,  "Beverages",  "2202",  28, 26),
    ("Thums Up",   "Soft Drink",             "750ml",  40,  "Beverages",  "2202",  28, 24),
    ("Sprite",     "Soft Drink",             "750ml",  40,  "Beverages",  "2202",  28, 19),
    ("Pepsi",      "Soft Drink",             "750ml",  40,  "Beverages",  "2202",  28, 17),
    ("Maaza",      "Mango Drink",            "600ml",  40,  "Beverages",  "2202",  12, 21),
    ("Frooti",     "Mango Drink",            "600ml",  40,  "Beverages",  "2202",  12, 18),
    ("Bisleri",    "Water",                  "1L",     20,  "Beverages",  "2201",  18, 38),
    ("Kinley",     "Water",                  "1L",     20,  "Beverages",  "2201",  18, 22),
    ("Real",       "Mixed Fruit Juice",      "1L",     125, "Beverages",  "2009",  12,  5),
    ("Tropicana",  "Orange Juice",           "1L",     125, "Beverages",  "2009",  12,  4),
    ("Colgate",    "Strong Teeth",           "100g",   55,  "Personal",   "3306",  18, 11),
    ("Colgate",    "MaxFresh",               "150g",   99,  "Personal",   "3306",  18,  6),
    ("Closeup",    "Red Hot",                "150g",   95,  "Personal",   "3306",  18,  5),
    ("Pepsodent",  "Germicheck",             "100g",   55,  "Personal",   "3306",  18,  7),
    ("Dabur",      "Red Paste",              "100g",   55,  "Personal",   "3306",  18,  9),
    ("Lifebuoy",   "Soap",                   "125g",   35,  "Personal",   "3401",  18, 24),
    ("Lux",        "Soap",                   "100g",   35,  "Personal",   "3401",  18, 21),
    ("Dettol",     "Soap",                   "125g",   45,  "Personal",   "3401",  18, 16),
    ("Santoor",    "Soap",                   "125g",   35,  "Personal",   "3401",  18, 14),
    ("Medimix",    "Soap",                   "125g",   40,  "Personal",   "3401",  18,  8),
    ("Clinic Plus","Shampoo",                "175ml",  99,  "Personal",   "3305",  18,  9),
    ("Head&Shoulders","Anti-Dandruff",       "180ml",  185, "Personal",   "3305",  18,  4),
    ("Dove",       "Shampoo",                "180ml",  165, "Personal",   "3305",  18,  3),
    ("Clinic Plus","Shampoo Sachet",         "6ml",     3,  "Personal",   "3305",  18, 52),
    ("Chik",       "Shampoo Sachet",         "6ml",     3,  "Personal",   "3305",  18, 33),
    ("Nivea",      "Body Lotion",            "200ml",  199, "Personal",   "3304",  18,  3),
    ("Vaseline",   "Body Lotion",            "200ml",  185, "Personal",   "3304",  18,  4),
    ("Fair & Lovely","Face Cream",           "50g",    125, "Personal",   "3304",  18,  5),
    ("Gillette",   "Shaving Cream",          "70g",    99,  "Personal",   "3307",  18,  4),
    ("Surf Excel", "Easy Wash",              "1kg",    125, "Home Care",  "3402",  18, 10),
    ("Surf Excel", "Easy Wash",              "500g",   68,  "Home Care",  "3402",  18, 13),
    ("Rin",        "Detergent Bar",          "250g",   20,  "Home Care",  "3401",  18, 28),
    ("Wheel",      "Detergent Powder",       "1kg",    62,  "Home Care",  "3402",  18, 15),
    ("Tide",       "Detergent Powder",       "1kg",    115, "Home Care",  "3402",  18,  8),
    ("Ariel",      "Detergent Powder",       "1kg",    165, "Home Care",  "3402",  18,  5),
    ("Vim",        "Dishwash Bar",           "300g",   30,  "Home Care",  "3401",  18, 26),
    ("Vim",        "Dishwash Gel",           "500ml",  115, "Home Care",  "3402",  18,  7),
    ("Harpic",     "Toilet Cleaner",         "500ml",  99,  "Home Care",  "3402",  18,  6),
    ("Lizol",      "Floor Cleaner",          "500ml",  99,  "Home Care",  "3402",  18,  5),
    ("Colin",      "Glass Cleaner",          "500ml",  95,  "Home Care",  "3402",  18,  3),
    ("Good Knight","Mosquito Refill",        "45ml",   85,  "Home Care",  "3808",  18,  6),
    ("All Out",    "Mosquito Refill",        "45ml",   85,  "Home Care",  "3808",  18,  5),
    ("Dettol",     "Antiseptic Liquid",      "125ml",  75,  "Home Care",  "3808",  18,  4),
    ("Cadbury",    "Dairy Milk",             "50g",    45,  "Confection", "1806",  18, 23),
    ("Cadbury",    "5 Star",                 "40g",    20,  "Confection", "1806",  18, 27),
    ("Nestle",     "KitKat",                 "37g",    20,  "Confection", "1806",  18, 25),
    ("Cadbury",    "Perk",                   "35g",    20,  "Confection", "1806",  18, 20),
    ("Cadbury",    "Gems",                   "17g",    10,  "Confection", "1806",  18, 24),
    ("Mentos",     "Mint Roll",              "29g",    10,  "Confection", "1704",  18, 19),
    ("Polo",       "Mint Roll",              "28g",    10,  "Confection", "1704",  18, 16),
    ("Center Fresh","Chewing Gum",           "28g",    10,  "Confection", "1704",  18, 22),
    ("Alpenliebe", "Candy Pack",             "50g",    20,  "Confection", "1704",  18, 14),
    ("Nestle",     "Munch",                  "20g",    10,  "Confection", "1806",  18, 29),
    ("Everest",    "Garam Masala",           "100g",   75,  "Spices",     "0910",  5,   6),
    ("MDH",        "Chana Masala",           "100g",   75,  "Spices",     "0910",  5,   5),
    ("Everest",    "Turmeric Powder",        "200g",   68,  "Spices",     "0910",  5,   7),
    ("Catch",      "Red Chilli Powder",      "100g",   62,  "Spices",     "0910",  5,   6),
    ("Kissan",     "Mixed Fruit Jam",        "500g",   145, "Spreads",    "2007",  12,  4),
    ("Kissan",     "Tomato Ketchup",         "950g",   135, "Spreads",    "2103",  12,  6),
    ("Maggi",      "Hot & Sweet Sauce",      "500g",   115, "Spreads",    "2103",  12,  5),
    ("Sundrop",    "Peanut Butter",          "200g",   135, "Spreads",    "2008",  12,  3),
]

def build_catalogue():
    cat = []
    for i, (brand, name, pack, mrp, category, hsn, gst, base) in enumerate(CATALOGUE_SEED, start=1):
        cat.append({
            "sku_id": f"SKU{i:03d}",
            "brand": brand,
            "product_name": name,
            "pack_size": pack,
            "mrp": mrp,
            "selling_price": mrp,          # kiranas sell at MRP
            "category": category,
            "hsn_code": hsn,
            "gst_rate": gst,
            "base_daily_units": base,
            "opening_stock": max(6, int(base * RNG.uniform(1.8, 4.0))),
        })
    return cat


# --------------------------------------------------------------------------
# 2. SUPPLIERS
# --------------------------------------------------------------------------
SUPPLIERS = [
    {
        "supplier_id": "SUP01", "name": "Gupta Traders",
        "phone": "+91-98xxxxxx01", "upi": "guptatraders@paytm",
        "margin_off_mrp": 0.22,   # cheapest
        "moq_cartons": 1, "credit_days": 0, "lead_time_hours": 24,
        "reliability": 0.93,
    },
    {
        "supplier_id": "SUP02", "name": "Metro Wholesalers",
        "phone": "+91-98xxxxxx02", "upi": "metrowholesale@paytm",
        "margin_off_mrp": 0.25,   # best unit price but bulk only
        "moq_cartons": 5, "credit_days": 0, "lead_time_hours": 12,
        "reliability": 0.88,
    },
    {
        "supplier_id": "SUP03", "name": "Sharma Distributors",
        "phone": "+91-98xxxxxx03", "upi": "sharmadist@paytm",
        "margin_off_mrp": 0.18,   # dearest, but fast + credit
        "moq_cartons": 1, "credit_days": 7, "lead_time_hours": 2,
        "reliability": 0.96,
    },
]

def build_supplier_cards(catalogue):
    cards = []
    for s in SUPPLIERS:
        for item in catalogue:
            units = 24 if item["mrp"] < 100 else 12
            unit_cost = round(item["mrp"] * (1 - s["margin_off_mrp"]), 2)
            cards.append({
                "supplier_id": s["supplier_id"],
                "sku_id": item["sku_id"],
                "units_per_carton": units,
                "unit_cost": unit_cost,
                "carton_price": round(unit_cost * units, 2),
                "moq_cartons": s["moq_cartons"],
                "credit_days": s["credit_days"],
                "lead_time_hours": s["lead_time_hours"],
            })
    return cards


# --------------------------------------------------------------------------
# 3. TRANSACTIONS  (with ground truth)
# --------------------------------------------------------------------------
# Kirana reality modelled:
#   - two rush windows: 08:00-11:00 morning, 17:00-21:00 evening
#   - most payments are 1-item; a meaningful minority are 2-3 item baskets
#   - ~18% of sales are cash and never touch the Soundbox (invisible to us)

HOUR_WEIGHTS = {
    6: 0.2, 7: 0.6, 8: 1.6, 9: 1.8, 10: 1.5, 11: 1.0, 12: 0.8, 13: 0.7,
    14: 0.6, 15: 0.6, 16: 0.9, 17: 1.5, 18: 1.9, 19: 2.0, 20: 1.6, 21: 0.9, 22: 0.3,
}
BASKET_SIZE_PROBS = [(1, 0.62), (2, 0.26), (3, 0.12)]
CASH_SHARE = 0.18

def pick_basket_size():
    r = RNG.random(); acc = 0
    for n, p in BASKET_SIZE_PROBS:
        acc += p
        if r <= acc:
            return n
    return 1

def build_transactions(catalogue, days=30, start=None):
    start = start or (datetime(2026, 9, 17, 0, 0) - timedelta(days=days))
    weights = [it["base_daily_units"] for it in catalogue]
    txns, tid = [], 1

    for d in range(days):
        day = start + timedelta(days=d)
        # weekend uplift
        day_mult = 1.25 if day.weekday() >= 5 else 1.0
        # a mild festival bump in one week
        if 12 <= d <= 16:
            day_mult *= 1.18

        for hour, hw in HOUR_WEIGHTS.items():
            n_baskets = max(0, int(RNG.gauss(hw * 6 * day_mult, 1.8)))
            for _ in range(n_baskets):
                size = pick_basket_size()
                items = RNG.choices(catalogue, weights=weights, k=size)
                basket = {}
                for it in items:
                    basket[it["sku_id"]] = basket.get(it["sku_id"], 0) + 1
                amount = sum(
                    next(c["selling_price"] for c in catalogue if c["sku_id"] == sid) * q
                    for sid, q in basket.items()
                )
                ts = day.replace(hour=hour, minute=RNG.randint(0, 59),
                                 second=RNG.randint(0, 59))
                is_cash = RNG.random() < CASH_SHARE
                txns.append({
                    "transaction_id": f"TXN{tid:06d}",
                    "timestamp": ts.isoformat(),
                    "amount": round(float(amount), 2),
                    "channel": "CASH" if is_cash else "PAYTM_SOUNDBOX",
                    "visible_to_engine": not is_cash,
                    # ---- GROUND TRUTH (engine must never read these) ----
                    "truth_basket": basket,
                    "truth_primary_sku": max(
                        basket.items(),
                        key=lambda kv: kv[1] * next(
                            c["selling_price"] for c in catalogue if c["sku_id"] == kv[0])
                    )[0],
                })
                tid += 1

    txns.sort(key=lambda t: t["timestamp"])
    return txns


def main():
    catalogue = build_catalogue()
    suppliers = SUPPLIERS
    cards = build_supplier_cards(catalogue)
    txns = build_transactions(catalogue)

    (OUT / "catalogue.json").write_text(json.dumps(catalogue, indent=2))
    (OUT / "suppliers.json").write_text(json.dumps(
        {"suppliers": suppliers, "price_cards": cards}, indent=2))
    (OUT / "transactions.json").write_text(json.dumps(txns, indent=2))

    # ---- collision report: the reason this problem is hard ----
    from collections import Counter, defaultdict
    by_price = defaultdict(list)
    for it in catalogue:
        by_price[it["selling_price"]].append(f'{it["brand"]} {it["product_name"]} {it["pack_size"]}')
    collisions = {p: v for p, v in by_price.items() if len(v) > 1}

    visible = [t for t in txns if t["visible_to_engine"]]
    print(f"catalogue      : {len(catalogue)} SKUs")
    print(f"price cards    : {len(cards)} rows across {len(suppliers)} suppliers")
    print(f"transactions   : {len(txns)} over 30 days "
          f"({len(visible)} visible to engine, {len(txns)-len(visible)} cash)")
    print(f"colliding price points: {len(collisions)} "
          f"covering {sum(len(v) for v in collisions.values())} SKUs")
    worst = sorted(collisions.items(), key=lambda kv: -len(kv[1]))[:5]
    for price, names in worst:
        print(f"   Rs {price:>4} -> {len(names)} SKUs: {', '.join(names[:3])}"
              f"{' ...' if len(names) > 3 else ''}")

if __name__ == "__main__":
    main()
