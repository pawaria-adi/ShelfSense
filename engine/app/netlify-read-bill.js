// Netlify wrapper around ../../api/read-bill.js
const {readBill} = require("../../api/read-bill.js");
const cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "content-type, x-app-token"};
exports.handler = async event => {
  if (event.httpMethod === "OPTIONS") return {statusCode: 204, headers: cors};
  if (event.httpMethod !== "POST") return {statusCode: 405, headers: cors, body: JSON.stringify({error: "POST only"})};
  let body = null;
  try { body = JSON.parse(event.body || "{}"); } catch (e) {}
  const headers = Object.fromEntries(Object.entries(event.headers || {}).map(([k, v]) => [k.toLowerCase(), v]));
  try {
    const [status, json] = await readBill(body, headers);
    return {statusCode: status, headers: {...cors, "Content-Type": "application/json"}, body: JSON.stringify(json)};
  } catch (e) {
    return {statusCode: 502, headers: cors, body: JSON.stringify({error: "could not reach Claude"})};
  }
};
