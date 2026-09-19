"""
ShelfSense — build the deployable app (an installable web app / PWA).

    python3 engine/build_app.py        -> app/   (upload this folder to any web host)

app/ contains:
    index.html              the ShelfSense screen (same as the claude.ai version)
    config.js               your settings (kept if it already exists — your keys are safe)
    adapters.js             connects the screen to Supabase / the bill reader / downloads
    sw.js, manifest.webmanifest, icon-*.png    install to home screen + open offline
    api/read-bill.js        bill-photo reader for Vercel
    api/paytm-webhook.js    Paytm payment alerts -> shop data (optional)
    api/confirm-order.js    the wholesaler's "confirm order" link (optional)
    api/places.js           find nearby wholesalers with Mappls (optional)
    vendor/zxing.min.js     camera barcode reader for phones without a built-in one
    netlify/functions/…     the same for Netlify, plus netlify.toml
    supabase.sql            one-time database setup for multi-phone sync
    DEPLOY.md               step-by-step deployment guide

Run after engine/build_screen.py (run_all.py does both).
"""

import hashlib, json, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_screen  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "engine" / "app"
APP = ROOT / "app"
TEAL, PAPER, MARIGOLD = (14, 90, 85), (243, 245, 242), (229, 164, 11)


def icon(size, path, maskable_pad=0.0):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (size, size), TEAL)
    d = ImageDraw.Draw(img)
    pad = size * (0.2 + maskable_pad)
    w = size - 2 * pad
    # three shelves with goods; the top shelf has one gap being "sensed"
    for i in range(3):
        y = pad + w * (0.3 + i * 0.3)
        d.rounded_rectangle([pad, y, pad + w, y + w * 0.05], radius=size * 0.01, fill=PAPER)
        for j in range(4):
            if i == 0 and j == 2:
                continue
            x = pad + w * (0.06 + j * 0.24)
            h = w * (0.16 + 0.04 * ((i + j) % 2))
            d.rounded_rectangle([x, y - h, x + w * 0.16, y - w * 0.01], radius=size * 0.012, fill=PAPER)
    x = pad + w * (0.06 + 2 * 0.24)
    y = pad + w * 0.3
    d.ellipse([x, y - w * 0.2, x + w * 0.16, y - w * 0.04], outline=MARIGOLD, width=max(2, size // 48))
    img.save(path, optimize=True)


def main():
    body = build_screen.build()
    head_end = body.index("</style>") + len("</style>")
    head, rest = body[:head_end], body[head_end:]
    head = head.replace("<title>ShelfSense Morning Order</title>", "<title>ShelfSense</title>")
    version = hashlib.sha1(body.encode()).hexdigest()[:10]

    APP.mkdir(exist_ok=True)
    (APP / "index.html").write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0E5A55">
<meta name="description" content="ShelfSense: what to reorder, from whom, and what to sell before it expires — for small grocery shops.">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon-192.png">
<link rel="apple-touch-icon" href="icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<style>body{{margin:0}}[hidden]{{display:none!important}}:root{{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}}</style>
<script>window.SHELFSENSE_ZXING_URL = "vendor/zxing.min.js";</script>
<script src="config.js"></script>
<script src="adapters.js"></script>
{head}
</head><body>
{rest}
</body></html>
""")
    if not (APP / "config.js").exists():
        shutil.copy(SRC / "config.js", APP / "config.js")
    shutil.copy(SRC / "adapters.js", APP / "adapters.js")
    shutil.copy(SRC / "supabase.sql", APP / "supabase.sql")
    (APP / "sw.js").write_text((SRC / "sw.js").read_text().replace("__VERSION__", "shelfsense-" + version))
    (APP / "api").mkdir(exist_ok=True)
    shutil.copy(SRC / "read-bill.js", APP / "api" / "read-bill.js")
    (APP / "netlify" / "functions").mkdir(parents=True, exist_ok=True)
    shutil.copy(SRC / "netlify-read-bill.js", APP / "netlify" / "functions" / "read-bill.js")
    shutil.copy(SRC / "paytm-webhook.js", APP / "api" / "paytm-webhook.js")
    shutil.copy(SRC / "confirm-order.js", APP / "api" / "confirm-order.js")
    shutil.copy(SRC / "places.js", APP / "api" / "places.js")
    shutil.copy(SRC / "netlify-places.js", APP / "netlify" / "functions" / "places.js")
    shutil.copy(SRC / "netlify-confirm-order.js", APP / "netlify" / "functions" / "confirm-order.js")
    shutil.copy(SRC / "netlify-paytm-webhook.js", APP / "netlify" / "functions" / "paytm-webhook.js")
    (APP / "vendor").mkdir(exist_ok=True)
    for f in (SRC / "vendor").iterdir():          # barcode reader for phones without a built-in one
        shutil.copy(f, APP / "vendor" / f.name)
    (APP / "netlify.toml").write_text('''[build]
  publish = "."
  functions = "netlify/functions"

[[redirects]]
  from = "/api/read-bill"
  to = "/.netlify/functions/read-bill"
  status = 200

[[redirects]]
  from = "/api/paytm-webhook"
  to = "/.netlify/functions/paytm-webhook"
  status = 200

[[redirects]]
  from = "/api/confirm-order"
  to = "/.netlify/functions/confirm-order"
  status = 200

[[redirects]]
  from = "/api/places"
  to = "/.netlify/functions/places"
  status = 200
''')
    (APP / "package.json").write_text(json.dumps(
        {"name": "shelfsense", "private": True, "version": "0.1.0", "engines": {"node": ">=18"}}, indent=2) + "\n")
    (APP / "manifest.webmanifest").write_text(json.dumps({
        "name": "ShelfSense", "short_name": "ShelfSense",
        "description": "Reorder list, delivery check, stock and expiry reminders for small grocery shops.",
        "start_url": "./", "scope": "./", "display": "standalone", "orientation": "portrait",
        "background_color": "#F3F5F2", "theme_color": "#0E5A55", "lang": "en",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, indent=2, ensure_ascii=False))
    icon(192, APP / "icon-192.png")
    icon(512, APP / "icon-512.png")
    icon(512, APP / "icon-maskable-512.png", maskable_pad=0.08)
    shutil.copy(SRC / "DEPLOY.md", APP / "DEPLOY.md")
    print(f"wrote app/ (version {version}) — see app/DEPLOY.md")


if __name__ == "__main__":
    main()
