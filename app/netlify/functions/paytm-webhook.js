// Netlify wrapper around ../../api/paytm-webhook.js
const {handlePayment, parseBody} = require("../../api/paytm-webhook.js");
exports.handler = async event => {
  if (event.httpMethod !== "POST") return {statusCode: 405, body: JSON.stringify({error: "POST only"})};
  const raw = event.isBase64Encoded ? Buffer.from(event.body || "", "base64").toString() : event.body;
  const type = (event.headers || {})["content-type"] || (event.headers || {})["Content-Type"];
  try {
    const [status, json] = await handlePayment(parseBody(raw, type));
    return {statusCode: status, headers: {"Content-Type": "application/json"}, body: JSON.stringify(json)};
  } catch (e) {
    return {statusCode: 500, body: JSON.stringify({error: "webhook failed"})};
  }
};
