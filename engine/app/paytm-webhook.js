/*
 * ShelfSense — Paytm payment webhook (server function).
 *
 * Paytm calls this address after each successful payment (Paytm Payment Gateway /
 * business API accounts: Dashboard -> Developer Settings -> Webhook "Payment Status").
 * We check Paytm's signature, then save the payment to the shop's data in Supabase.
 * The app shows it under Billing station -> Paytm payments with the likely items.
 *
 * Environment variables (server only — never put these in config.js):
 *   PAYTM_MERCHANT_KEY          required: the merchant key from the Paytm dashboard
 *   PAYTM_MID                   optional: only accept payments for this merchant ID
 *   SHELFSENSE_SHOP_ID          required: the shop (Settings -> About shows it) the payments belong to
 *   SUPABASE_URL                required: same as supabaseUrl in config.js
 *   SUPABASE_SERVICE_ROLE_KEY   required: Supabase -> Project Settings -> API -> service_role
 *
 * Works as a Vercel function (api/paytm-webhook.js) and, through
 * netlify/functions/paytm-webhook.js, as a Netlify function.
 */
const crypto = require("crypto");

// Paytm checksum (same method as Paytm's "paytmchecksum" library)
const IV = "@@@@&&&&####$$$$";
function paramString(params) {
  return Object.keys(params).sort().map(k => (params[k] == null ? "" : String(params[k]))).join("|");
}
function verifyChecksum(params, key, checksum) {
  try {
    const d = crypto.createDecipheriv("AES-128-CBC", key, IV);
    const hash = d.update(checksum, "base64", "binary") + d.final("binary");
    const salt = hash.slice(-4);
    const expect = crypto.createHash("sha256").update(paramString(params) + "|" + salt).digest("hex") + salt;
    return hash.length === expect.length && crypto.timingSafeEqual(Buffer.from(hash), Buffer.from(expect));
  } catch (e) { return false; }
}
function makeChecksum(params, key, salt = crypto.randomBytes(3).toString("base64")) {   // for tests
  const c = crypto.createCipheriv("AES-128-CBC", key, IV);
  const hash = crypto.createHash("sha256").update(paramString(params) + "|" + salt).digest("hex") + salt;
  return c.update(hash, "binary", "base64") + c.final("base64");
}

// "2026-09-17 15:13:02.0" (India time) -> ISO
function paytmTime(s) {
  const m = String(s || "").match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})/);
  return m ? new Date(`${m[1]}T${m[2]}+05:30`).toISOString() : new Date().toISOString();
}
const indiaDay = iso => new Date(Date.parse(iso) + 330 * 60000).toISOString().slice(0, 10);

async function handlePayment(fields) {
  const env = process.env;
  for (const k of ["PAYTM_MERCHANT_KEY", "SHELFSENSE_SHOP_ID", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"])
    if (!env[k]) return [500, {error: `${k} is not set on the server`}];
  const params = {...fields};
  const checksum = params.CHECKSUMHASH;
  delete params.CHECKSUMHASH;
  if (!checksum || !verifyChecksum(params, env.PAYTM_MERCHANT_KEY, checksum)) return [401, {error: "bad checksum"}];
  if (env.PAYTM_MID && params.MID !== env.PAYTM_MID) return [403, {error: "unknown merchant"}];
  if (params.STATUS !== "TXN_SUCCESS") return [200, {ok: true, skipped: params.STATUS || "no status"}];
  const amount = Number(params.TXNAMOUNT);
  const id = String(params.TXNID || params.ORDERID || "").replace(/[^A-Za-z0-9_.~:@+-]/g, "").slice(0, 120);
  if (!(amount > 0) || !id) return [400, {error: "missing amount or transaction id"}];
  const at = paytmTime(params.TXNDATE);
  const rec = {id, amount, at, day: indiaDay(at), source: "paytm", status: "new",
               mode: params.PAYMENTMODE || null, order_id: params.ORDERID || null};
  const res = await fetch(`${env.SUPABASE_URL.replace(/\/$/, "")}/rest/v1/docs?on_conflict=path`, {
    method: "POST",
    headers: {apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: "Bearer " + env.SUPABASE_SERVICE_ROLE_KEY,
              "Content-Type": "application/json",
              Prefer: "resolution=ignore-duplicates,return=minimal"},   // Paytm may call twice: keep the first
    body: JSON.stringify({path: `${env.SHELFSENSE_SHOP_ID}/payments/${id}`, data: rec, updated_at: new Date().toISOString()}),
  });
  if (!res.ok) return [502, {error: `could not save (${res.status})`}];
  return [200, {ok: true}];
}

function parseBody(raw, contentType) {
  if (raw && typeof raw === "object") return raw;
  const text = String(raw || "");
  if (/json/i.test(contentType || "") || text.trim().startsWith("{")) { try { return JSON.parse(text); } catch (e) { return {}; } }
  return Object.fromEntries(new URLSearchParams(text));
}

// Vercel (Node) handler
module.exports = async (req, res) => {
  if (req.method !== "POST") return res.status(405).json({error: "POST only"});
  try {
    const [status, json] = await handlePayment(parseBody(req.body, req.headers["content-type"]));
    res.status(status).json(json);
  } catch (e) {
    res.status(500).json({error: "webhook failed"});
  }
};
module.exports.handlePayment = handlePayment;
module.exports.parseBody = parseBody;
module.exports.makeChecksum = makeChecksum;
module.exports.verifyChecksum = verifyChecksum;
