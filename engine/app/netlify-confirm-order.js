// Netlify wrapper around ../../api/confirm-order.js
const {confirmOrder} = require("../../api/confirm-order.js");
exports.handler = async event => {
  try {
    const [status, html] = await confirmOrder(event.queryStringParameters || {});
    return {statusCode: status, headers: {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}, body: html};
  } catch (e) {
    return {statusCode: 502, headers: {"Content-Type": "text/html; charset=utf-8"},
            body: "<h1>Something went wrong</h1><p>Please try the link again in a minute.</p>"};
  }
};
