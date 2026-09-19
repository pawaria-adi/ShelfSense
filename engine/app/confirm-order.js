/*
 * ShelfSense — order confirmation page (server function).
 *
 * The WhatsApp order message ends with a link like
 *   https://YOUR-SITE/api/confirm-order?o=<shop id>~<order id>&t=<token>
 * The wholesaler taps it: this page checks the token against the saved order, marks the
 * order "confirmed" straight away, and shows what was ordered. The shop's app picks the
 * change up on its next sync, and the Deliveries tab turns that order Active.
 *
 * Environment variables (server only):
 *   SUPABASE_URL                same as supabaseUrl in config.js
 *   SUPABASE_SERVICE_ROLE_KEY   Supabase -> Project Settings -> API -> service_role
 *
 * Nobody can confirm an order without the token, and the token only ever confirms:
 * it cannot read or change anything else in the shop's data.
 */
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

async function sb(path, opts = {}) {
  const env = process.env;
  const r = await fetch(`${env.SUPABASE_URL.replace(/\/$/, "")}/rest/v1/${path}`, {
    ...opts,
    headers: {apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: "Bearer " + env.SUPABASE_SERVICE_ROLE_KEY,
              "Content-Type": "application/json", ...(opts.headers || {})},
  });
  if (!r.ok) throw new Error(`supabase ${r.status}`);
  const text = await r.text();
  return text ? JSON.parse(text) : null;
}

function page(title, body, ok = true) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${esc(title)} · ShelfSense</title>
<style>body{margin:0;font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#F3F5F2;color:#12211F;padding:24px}
.card{max-width:520px;margin:6vh auto;background:#fff;border:1px solid #DDE3DE;border-radius:18px;padding:22px}
h1{font-size:1.35rem;margin:0 0 6px;color:${ok ? "#0E5A55" : "#A3341F"}}
.tick{font-size:2.2rem;line-height:1}
table{border-collapse:collapse;width:100%;margin-top:14px;font-size:.95rem}
td,th{text-align:left;padding:6px 4px;border-bottom:1px solid #EEF1EE}
td.r,th.r{text-align:right}
.muted{color:#5B6B66;font-size:.85rem}</style></head>
<body><div class="card">${body}</div></body></html>`;
}

async function confirmOrder(query) {
  const env = process.env;
  for (const k of ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"])
    if (!env[k]) return [500, page("Not set up", `<h1>Not set up</h1><p>${k} is missing on the server.</p>`, false)];
  const ref = String(query.o || ""), token = String(query.t || "");
  const [shop, key] = ref.split("~");
  if (!shop || !key || !token || !/^[\w.:@+-]+$/.test(shop) || !/^[\w.:@+-]+$/.test(key))
    return [400, page("Bad link", `<h1>Bad link</h1><p>This confirmation link is incomplete.</p>`, false)];
  const path = `${shop}/orders/${key}`;
  const rows = await sb(`docs?path=eq.${encodeURIComponent(path)}&select=data`);
  const order = rows?.[0]?.data;
  if (!order) return [404, page("Order not found", `<h1>Order not found</h1><p>It may have been cancelled.</p>`, false)];
  if (!order.token || order.token !== token)
    return [403, page("Link doesn't match", `<h1>Link doesn't match</h1><p>Ask the shop to send the order again.</p>`, false)];
  const already = order.status === "confirmed";
  if (!already) {
    const next = {...order, status: "confirmed", confirmed_at: new Date().toISOString(), confirmed_by: "supplier link"};
    await sb("docs?on_conflict=path", {method: "POST", headers: {Prefer: "resolution=merge-duplicates,return=minimal"},
      body: JSON.stringify({path, data: next, updated_at: new Date().toISOString()})});
  }
  const lines = (order.lines || []).map(l => `<tr><td>${esc(l.name || l.sku_id)}</td><td class="r">${esc(l.packs ?? "")} ${l.packs ? "×" : ""} ${esc(l.pack_size || "")}</td><td class="r">${esc(l.qty)} pcs</td></tr>`).join("");
  return [200, page("Order confirmed", `<div class="tick">✅</div>
    <h1>${already ? "Already confirmed" : "Order confirmed"}</h1>
    <p>Thank you — the shop can see this order as confirmed${already ? "" : " now"}.</p>
    <table><thead><tr><th>Item</th><th class="r">Cases</th><th class="r">Pieces</th></tr></thead><tbody>${lines}</tbody></table>
    ${order.total ? `<p class="muted">About ₹${esc(order.total)} · order ${esc(order.date || "")}</p>` : ""}
    <p class="muted">Sent from ShelfSense.</p>`)];
}

// Vercel (Node) handler
module.exports = async (req, res) => {
  const url = new URL(req.url, "http://x");
  const query = Object.fromEntries(url.searchParams);
  try {
    const [status, html] = await confirmOrder(query);
    res.statusCode = status;
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.end(html);
  } catch (e) {
    res.statusCode = 502;
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.end(page("Something went wrong", "<h1>Something went wrong</h1><p>Please try the link again in a minute.</p>", false));
  }
};
module.exports.confirmOrder = confirmOrder;
