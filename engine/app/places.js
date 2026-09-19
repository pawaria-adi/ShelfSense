/*
 * ShelfSense — "find wholesalers near me" (server function, Mappls / MapmyIndia).
 *
 * The app posts {place} (the shop's town + PIN code) or {refLocation} and gets back a
 * list of nearby wholesale businesses with name, address and phone number. Mappls keys
 * stay on the server: the app never sees them.
 *
 * Environment variables (server only) — one of these two ways of signing in:
 *   MAPPLS_CLIENT_ID + MAPPLS_CLIENT_SECRET   REST API credentials (apis.mappls.com -> your project)
 *   MAPPLS_KEY                                a single REST key, if your account still uses those
 *   APP_TOKEN              optional; if set, the app must send the same value (config.js appToken)
 *
 * Endpoints used (Mappls REST APIs):
 *   POST https://outpost.mappls.com/api/security/oauth/token   grant_type=client_credentials
 *   GET  https://atlas.mappls.com/api/places/textsearch/json?query=...      (find the town)
 *   GET  https://atlas.mappls.com/api/places/nearby/json?keywords=...&refLocation=...
 *
 * Mappls data is licensed by Mappls: show "Powered by Mappls" where results appear, and
 * don't build your own copy of their directory — the app only keeps the wholesalers the
 * shopkeeper picks.
 */
const TOKEN_URL = "https://outpost.mappls.com/api/security/oauth/token";
const ATLAS = "https://atlas.mappls.com/api/places";
const LEGACY = "https://apis.mappls.com/advancedmaps/v1";   // single-key accounts
const KEYWORDS = ["wholesale", "cash and carry", "kirana store", "grocery store", "distributor"];
const MAX_RESULTS = 25;

let cached = {token: null, expires: 0};

async function token() {
  const env = process.env;
  if (cached.token && Date.now() < cached.expires - 60000) return cached.token;
  const body = new URLSearchParams({grant_type: "client_credentials", client_id: env.MAPPLS_CLIENT_ID,
                                    client_secret: env.MAPPLS_CLIENT_SECRET});
  const r = await fetch(TOKEN_URL, {method: "POST", headers: {"Content-Type": "application/x-www-form-urlencoded"}, body});
  if (!r.ok) throw new Error(`mappls token ${r.status}`);
  const j = await r.json();
  if (!j.access_token) throw new Error("mappls token missing");
  cached = {token: j.access_token, expires: Date.now() + (Number(j.expires_in) || 3600) * 1000};
  return cached.token;
}

const LEGACY_PATH = {"textsearch/json": "textsearch", "nearby/json": "nearby_search"};

async function atlas(path, params) {
  const env = process.env;
  let url, opts = {};
  if (env.MAPPLS_KEY && !env.MAPPLS_CLIENT_ID) {
    url = `${LEGACY}/${env.MAPPLS_KEY}/${LEGACY_PATH[path] || path}?` + new URLSearchParams(params);
  } else {
    url = `${ATLAS}/${path}?` + new URLSearchParams(params);
    opts = {headers: {Authorization: "bearer " + (await token())}};
  }
  const r = await fetch(url, opts);
  if (r.status === 401 || r.status === 412) {
    cached = {token: null, expires: 0};
    const detail = (await r.text()).slice(0, 200);
    throw new Error(`mappls refused the key (${r.status}) ${detail}`);
  }
  if (!r.ok) throw new Error(`mappls ${path} ${r.status}`);
  return r.json();
}

const list = j => j?.suggestedLocations || j?.results || [];
const phoneOf = p => String(p.mobileNo || p.landlineNo || "").split(",")[0].replace(/[^\d+]/g, "");

// the town/PIN code -> a reference point Mappls understands (an eLoc, or lat,lng)
async function findPlace(place) {
  const j = await atlas("textsearch/json", {query: place, region: "IND"});
  const hit = list(j)[0];
  if (!hit) return null;
  const ref = hit.latitude != null && hit.longitude != null ? `${hit.latitude},${hit.longitude}` : hit.eLoc;
  return ref ? {ref, label: [hit.placeName, hit.placeAddress].filter(Boolean).join(", ")} : null;
}

async function nearby(ref, radius) {
  const seen = new Map();
  for (const keywords of KEYWORDS) {
    let j;
    try { j = await atlas("nearby/json", {keywords, refLocation: ref, radius, sortBy: "dist:asc", region: "IND"}); }
    catch (e) { continue; }                       // one keyword failing shouldn't lose the rest
    for (const p of list(j)) {
      const id = p.eLoc || `${p.placeName}|${p.placeAddress}`;
      if (!p.placeName || seen.has(id)) continue;
      seen.set(id, {name: String(p.placeName).slice(0, 60), address: String(p.placeAddress || "").slice(0, 120),
                    phone: phoneOf(p), km: Math.round((Number(p.distance) || 0) / 100) / 10, eloc: p.eLoc || null,
                    kind: keywords, source: "mappls"});
    }
    if (seen.size >= MAX_RESULTS) break;
  }
  return [...seen.values()].sort((a, b) => a.km - b.km).slice(0, MAX_RESULTS);
}

async function findWholesalers(body, headers) {
  const env = process.env;
  if (!env.MAPPLS_KEY && (!env.MAPPLS_CLIENT_ID || !env.MAPPLS_CLIENT_SECRET))
    return [500, {error: "Set MAPPLS_CLIENT_ID + MAPPLS_CLIENT_SECRET (or MAPPLS_KEY) on the server"}];
  if (env.APP_TOKEN && headers["x-app-token"] !== env.APP_TOKEN) return [401, {error: "wrong app token"}];
  const place = String(body?.place || "").slice(0, 120);
  const given = String(body?.refLocation || "").slice(0, 60);
  const radius = Math.min(10000, Math.max(500, Number(body?.radius) || 5000));
  if (!place && !given) return [400, {error: "send the shop's town or PIN code"}];
  let ref = given, label = null;
  if (!ref) {
    const found = await findPlace(place);
    if (!found) return [404, {error: "that place wasn't found", place}];
    ref = found.ref; label = found.label;
  }
  return [200, {place: label, refLocation: ref, radius, results: await nearby(ref, radius)}];
}

// Vercel (Node) handler
module.exports = async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "content-type, x-app-token");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "POST") return res.status(405).json({error: "POST only"});
  let body = req.body;
  if (typeof body === "string") { try { body = JSON.parse(body); } catch (e) { body = null; } }
  try {
    const [status, json] = await findWholesalers(body, req.headers || {});
    res.status(status).json(json);
  } catch (e) {
    res.status(502).json({error: "could not reach Mappls", detail: String(e).slice(0, 200)});
  }
};
module.exports.findWholesalers = findWholesalers;
module.exports.resetToken = () => { cached = {token: null, expires: 0}; };
