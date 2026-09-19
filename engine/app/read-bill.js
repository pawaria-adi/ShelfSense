/*
 * ShelfSense — bill reader (server function).
 * Receives {prompt, images:[{media_type, data(base64)}]} from the app, asks Claude,
 * returns {text}. The API key stays on the server; it is never sent to phones.
 *
 * Environment variables:
 *   ANTHROPIC_API_KEY   required
 *   ANTHROPIC_MODEL     optional, default claude-sonnet-5
 *   APP_TOKEN           optional; if set, the app must send the same value
 *                       (config.js appToken) — stops strangers using your key
 *   ANTHROPIC_BASE_URL  optional (testing)
 *
 * Works as a Vercel function (api/read-bill.js) and, through the small wrapper in
 * netlify/functions/read-bill.js, as a Netlify function.
 */
const MAX_PROMPT = 64 * 1024;
const MAX_IMAGES = 5;
const MAX_IMAGE_B64 = 7 * 1024 * 1024;

async function readBill(body, headers) {
  const env = process.env;
  if (!env.ANTHROPIC_API_KEY) return [500, {error: "ANTHROPIC_API_KEY is not set on the server"}];
  if (env.APP_TOKEN && headers["x-app-token"] !== env.APP_TOKEN) return [401, {error: "wrong app token"}];
  const {prompt, images = []} = body || {};
  if (typeof prompt !== "string" || !prompt || prompt.length > MAX_PROMPT) return [400, {error: "bad prompt"}];
  if (!Array.isArray(images) || images.length > MAX_IMAGES) return [400, {error: "too many images"}];
  const content = [];
  for (const im of images) {
    if (!/^image\/(jpeg|png|webp|gif)$/.test(im?.media_type || "") || typeof im.data !== "string" || im.data.length > MAX_IMAGE_B64)
      return [400, {error: "bad image"}];
    content.push({type: "image", source: {type: "base64", media_type: im.media_type, data: im.data}});
  }
  content.push({type: "text", text: prompt});
  const res = await fetch((env.ANTHROPIC_BASE_URL || "https://api.anthropic.com") + "/v1/messages", {
    method: "POST",
    headers: {"content-type": "application/json", "x-api-key": env.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
    body: JSON.stringify({
      model: env.ANTHROPIC_MODEL || "claude-sonnet-5",
      max_tokens: 8000,
      system: "You read supplier delivery bills for a small Indian grocery shop and reply with JSON only, exactly in the shape the user asks for.",
      messages: [{role: "user", content}],
    }),
  });
  if (res.status === 429) return [429, {error: "busy, try again in a minute"}];
  if (!res.ok) return [502, {error: `Claude API error ${res.status}`, detail: (await res.text()).slice(0, 500)}];
  const out = await res.json();
  const text = (out.content || []).filter(b => b.type === "text").map(b => b.text).join("");
  return [200, {text}];
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
    const [status, json] = await readBill(body, req.headers);
    res.status(status).json(json);
  } catch (e) {
    res.status(502).json({error: "could not reach Claude"});
  }
};
module.exports.readBill = readBill;
