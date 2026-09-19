# Deploying ShelfSense

This folder is the whole app. It is an installable web app (PWA): judges open a
link, tap **Add to Home screen**, and it behaves like an app — its own icon, full
screen, and it opens without internet.

Pick the level you need. Each one builds on the previous one.

| Level | What you get | Time | Accounts needed |
|---|---|---|---|
| 1 | Working app on a public link, installable, offline, mobile + PIN login, data kept on each phone | 2 min | Netlify (free) |
| 2 | + cloud accounts (mobile + PIN), shops with team members, several phones share one shop, offline sync | +8 min | Supabase (free) |
| 3 | + bill photos read automatically, Paytm webhook, order confirm links, wholesaler search | +5 min | Vercel or Netlify (free) + Anthropic API key (+ free Mappls keys) |
| 4 | + an Android app file (APK / Play Store bundle) | +5 min | none (PWABuilder) |

---

## Level 1 — public link in 2 minutes

1. Go to **app.netlify.com/drop** (sign in with GitHub or email).
2. Drag this **app** folder onto the page.
3. You get a link like `https://shelfsense-xyz.netlify.app`. Open it on a phone:
   - Android Chrome: menu ⋮ → **Add to Home screen** / **Install app**
   - iPhone Safari: Share → **Add to Home Screen**

**Login at this level:** the app opens on **Log in** (mobile number + PIN). New shops tap
**Sign up**: shop name, owner name, a 10-digit mobile number (the +91 is already shown)
and a 4–6 digit PIN. A new shop starts **empty** — no practice numbers. The owner adds
staff (name, mobile, PIN) in **Settings → Shop & team**. PINs are stored scrambled
(PBKDF2), wrong PINs lock the login for a while, and the app can lock itself after
1/5/15 idle minutes.

**Example data (for judges):** log in with **+91 1234567890** and PIN **3456**. Only this
account opens the example shop ("Demo Kirana Store") with the practice orders, stock,
expiry alerts and sample bill. Its PIN can't be changed in the app, and because a
4-digit PIN in a public web page can be guessed, never put real shop data in it.
(To change it, edit `ADMIN_MOBILE` / `ADMIN_PIN` in `engine/build_screen.py` and rebuild.)

Data stays on each phone. Bill photos can't be read yet ("Enter by hand" works).
Tip: add `?reset=1` to the link to start from a clean slate.

Test on your own computer first:
```
cd app
python3 -m http.server 8080
```
then open http://localhost:8080 (install and offline work on localhost too).

## Level 2 — several phones, one shop (Supabase)

1. **supabase.com** → New project (free). Wait for it to start.
2. **SQL Editor** → New query → paste all of `supabase.sql` → **Run**.
3. **Authentication → Sign In / Providers → Email**: keep it on and turn **off
   "Confirm email"**. (No SMS or email is sent: the app turns the mobile number into an
   internal login name like `919876543210@phone.shelfsense.app`, and the PIN into the
   password. Leave the minimum password length at 6 or lower.)
4. **Project Settings → API**: copy the **Project URL** and the **anon public** key.
5. Open `config.js` and fill in:
   ```js
   supabaseUrl: "https://YOURPROJECT.supabase.co",
   supabaseAnonKey: "eyJ...",
   ```
6. Deploy the folder again (drag it onto Netlify Drop again, or redeploy).

**Login at this level:** **Sign up** asks for the name, 10-digit mobile number and PIN,
plus either a new shop name (they become its owner) or a shop's 6-character join code
(they join as staff). **Log in** needs only the mobile number and PIN. The owner finds
the code, a WhatsApp invite button and the team list (with mobile numbers) in
**Settings → Shop & team**, and can remove members or make a new code. New shops start
empty. The example account (+91 1234567890, PIN 3456) is created in Supabase the first
time someone logs in with it, together with the example shop.

Each shop's data is only readable and writable by its members (row-level security
in `supabase.sql`). Phones stay signed in, so the app still opens offline; changes
made offline wait on the phone and sync when it's back online.

Note: if you ran an older `supabase.sql`, run the new one again — it adds the mobile
column and replaces the shop functions.

## Level 3 — read bill photos (needs a server function)

Netlify Drop can't run server functions, so use one of these instead.

**Vercel (simplest):**
```
cd app
npx vercel            # first time: log in, accept the defaults
npx vercel env add ANTHROPIC_API_KEY      # paste your key from console.anthropic.com
npx vercel env add APP_TOKEN              # optional: any password-like text
npx vercel --prod
```
Then in `config.js` set `billReaderUrl: "/api/read-bill"` (and `appToken` to the
same text as APP_TOKEN, if you set one) and run `npx vercel --prod` again.

**Netlify (with Git or the CLI):**
```
cd app
npx netlify-cli deploy --prod     # uses netlify.toml, deploys the function too
```
Set `ANTHROPIC_API_KEY` (and optionally `APP_TOKEN`) under Site settings →
Environment variables, set `billReaderUrl: "/api/read-bill"` in `config.js`, deploy again.

Optional: `ANTHROPIC_MODEL` (default `claude-sonnet-5`).
The API key only lives on the server; it is never sent to phones.

## Paytm payment alerts (optional)

**Billing station → Paytm payments** turns each payment into a likely bill (the item
combination that adds up to the amount, ranked by how well each item sells, the time of
day and what's on the shelf). The shopkeeper taps **Confirm**, picks another guess,
**Edit items**, or **Not a sale**. Confirmed payments take the items off stock. Payments
arrive in one of three ways:

1. **Listen to Soundbox** (no setup): keep the phone near the Paytm Soundbox. The app
   hears "Paytm par ₹120 prapt hue" / "Received rupees 120 on Paytm" and adds ₹120.
   Needs Chrome on Android (speech recognition) and microphone permission; the screen
   must stay open. Chrome sends the audio to Google's speech service to turn it into text.
2. **Type the amount** — for testing or when the phone missed one.
3. **Paytm webhook** (Level 2 + a Paytm Payment Gateway / business API account — a plain
   Soundbox/QR account doesn't send webhooks):
   - Deploy on Vercel or Netlify (the function `api/paytm-webhook.js` is included).
   - Set server environment variables: `PAYTM_MERCHANT_KEY`, `PAYTM_MID` (optional),
     `SHELFSENSE_SHOP_ID` (owner: **Settings → About → Shop ID**), `SUPABASE_URL`,
     `SUPABASE_SERVICE_ROLE_KEY` (Supabase → Project Settings → API → service_role;
     server only, never in `config.js`).
   - In the Paytm dashboard, set the Payment Status webhook to
     `https://YOUR-SITE/api/paytm-webhook`.
   - Every call is checked against Paytm's signature; failed payments and other merchant
     IDs are ignored; a repeated call doesn't reset a payment already confirmed.

Guessing a bill from an amount is often wrong when many items share a price (our tests
on practice data: the top guess named the main item about 3 times in 10), so the app
never takes items off stock until someone confirms.

## Shop details, wholesalers and price lists

**Sign up** asks only for the shop name, mobile number and PIN. The next page asks for the
shopkeeper's name, town/city, PIN code, shop address and a **security question**. On
phone-only accounts that answer is the way back in: **Forgot PIN → mobile number → answer
→ new PIN** (the answer is stored scrambled, like the PIN). Cloud accounts can't reset a
PIN that way — the owner removes the member and they sign up again.

**Settings → Shop & location** edits the town, PIN code and address at any time.

**Settings → Wholesalers**:
- **Find wholesalers nearby** looks the shop up on the map and lists wholesale,
  cash-and-carry and kirana businesses within 5 km. Tap one to add it as a supplier;
  anything missing can be typed in. Two sources, in this order:
  - **Mappls (MapmyIndia)** — recommended for India, because its listings include phone
    numbers. Setup: sign up free at **apis.mappls.com**, create a project, copy the REST
    API **client ID and client secret**; deploy this folder as a site with functions
    (Vercel or Netlify, same as the bill reader); set `MAPPLS_CLIENT_ID` and
    `MAPPLS_CLIENT_SECRET` as server environment variables (never in `config.js`); then
    set `placesUrl: "/api/places"` in `config.js`. The keys stay on the server — the app
    only ever calls your own address. Show the "Powered by Mappls" credit the app
    displays, and keep only the wholesalers the shopkeeper picks (don't build a copy of
    their directory). Free-tier limits are per Mappls account.
  - **A single Mappls REST key** instead of the server function: put it in `config.js` as
    `mapplsKey: "..."`. The installed app then calls Mappls directly — no server needed —
    but the key is visible to anyone who opens the site, so restrict it to your domain in
    the Mappls console and prefer the server function for anything public. Note: the
    **Map SDK / JS key is not a REST key**; a wrong key type comes back as
    `CLIENT_CREDENTIAL_EXPIRED` / HTTP 412 and the app says the key wasn't accepted.
  - **OpenStreetMap** (Nominatim + Overpass) — used when `placesUrl` is empty. Free, no
    key, but thin on small wholesalers and usually no phone numbers. Both are community
    services: fair use only, roughly one search at a time.
  - Justdial and similar directories forbid copying their listings and have no public
    API, so they are not used — and "Justdial API" services are scrapers with the same
    problem.
- **Price list photo** (needs the bill reader, Level 3) reads a wholesaler's rate list and
  saves a rate, case size and minimum order per product. Each wholesaler's row then shows
  how many rates it has, how many of them are the cheapest, when the list was updated, and
  how many deliveries arrived complete.
- With price lists in place, the **Order** tab builds the daily list itself: it orders the
  products that run out before the next delivery, works out how many cases, and picks the
  cheapest wholesaler that lists each product (faster delivery breaks a tie), exactly as
  the example shop's list does. Tap **Why?** on any line to see the comparison.

## Order confirmation links

Each WhatsApp order ends with a link the wholesaler taps to confirm:
- **Online version:** deploy on Vercel or Netlify (the function `api/confirm-order.js` is
  included), set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` on the server, and put the
  public address in `config.js` as `confirmUrl`
  (e.g. `"https://yoursite.netlify.app/api/confirm-order"`). The wholesaler sees the order
  and it turns **Active** in the shop's app straight away. Each order carries its own
  random token: without it nothing can be confirmed, and the token can't read or change
  anything else.
- **Without the server function** the link only works on a phone already signed in to the
  shop, which is fine for a demo.
- **Deliveries tab:** confirmed orders come first and are marked **Active**; bills (photo
  or by hand) can only be entered for those. If the wholesaler confirmed by phone instead,
  tap **Wholesaler confirmed by phone** on that order.

## Product list (Open Food Facts)

"Add a product without scanning" searches the shop's own products first, then about
5,700 products sold in India from **Open Food Facts** (openfoodfacts.org, a free,
crowd-sourced database; list in `data/open_food_facts/`). Picking one asks for the
selling price and adds it to the shop's products (shared with the team, usable in
counts, sales, deliveries and payment guesses). A shop can also add its own products
by name.

- Scanning a barcode nobody knows: the app checks the bundled list, then asks the Open
  Food Facts website about that one barcode (the barcode is the only thing sent). If it
  isn't found, pick an existing product (the barcode gets linked) or add it by name.
  The claude.ai page may not be allowed to reach the website; the installed app can.
- Credit and licence: the data is under the Open Database License (ODbL). The app shows
  the credit line; if you share an improved list, share it under the ODbL too.
- Refresh the list: `python3 data/build_off_products.py --refresh` (downloads the
  nightly export, about 1 GB), then `python3 engine/run_all.py`.
- Why not Blinkit/Zepto/BigBasket: their terms don't allow copying their listings or
  using scrapers, and they offer no public product API.

## Barcode scanning

- **Stock → Start shelf count** (or **Count stock**): scan every item on the shelf — each
  scan adds 1 — or import a stock list CSV (barcode or name, quantity, optional expiry).
  Save as "This is what's on the shelf" (sets the numbers) or "Add to current stock".
  The unsaved count is kept on the phone if the app is closed.
- **Camera** button (Stock count and Billing station): uses the phone's built-in barcode
  reader when it has one (Android Chrome), otherwise the bundled ZXing reader
  (`vendor/zxing.min.js`, works offline). Needs https and camera permission. Inside the
  claude.ai page the camera may be blocked by the browser — use the installed app.
- USB/Bluetooth barcode scanners work everywhere: tap the box and scan.
- Demo files for a new shop are in the `demo` folder (stock list, a week of sales,
  printable barcodes, and a step-by-step README).

## Level 4 — Android app file

1. Deploy (any level above) so you have an `https://` link.
2. Go to **pwabuilder.com**, paste the link, click **Package for stores → Android**.
3. Download: the `.apk` installs directly on any Android phone (for judges);
   the `.aab` is what the Google Play Console accepts.

This wraps the same web app (a "Trusted Web Activity"), so updates to the website
reach the app without a new download.

---

## Updating the app

After changing anything in ShelfSense, from the project folder run:
```
python3 engine/run_all.py      # or just: python3 engine/build_app.py
```
then redeploy the `app` folder. Your `config.js` is never overwritten.

## What judges should try (2-minute demo)

0. Log in with **+91 1234567890**, PIN **3456** (the example shop).
1. **Order** tab: today's list per supplier → **Send on WhatsApp**.
2. **Deliveries**: **Try with a sample bill** (Level 3), or **No bill? Enter by hand** → confirm.
3. **Stock**: **Use soon** shows batches about to expire unsold → **Remove** or **Change date**.
4. **Billing station**: **Scan a sample item** twice → **Finish sale**.
5. (Level 2) Turn on airplane mode, make a change, turn it off → the header goes
   *Offline · 1 waiting* → *Synced*. Open the app in airplane mode: it still starts.
6. Switch **EN / हिं**, and in **Settings** try Dark theme and Large text.
7. **Settings → Lock** (Level 1), then unlock with the PIN.
8. Sign out → **Sign up** with any other number: the new shop starts empty.
