/*
 * ShelfSense — standalone adapters.
 *
 * The ShelfSense screen talks to its services through window.claude.use(name):
 *   "db"        shared, saved shop data (stock, deliveries, orders, scans...)
 *   "sample"    reads bill photos with Claude
 *   "downloads" saves a file for the computer programs
 * and, in this standalone app, to accounts through window.SHELFSENSE_AUTH.
 *
 *   With SHELFSENSE_CONFIG.supabaseUrl set: mobile number + PIN accounts, shops with
 *   members, and each shop's data synced through Supabase.
 *   (Supabase is used without SMS: the mobile number becomes an internal login name
 *   like 919876543210@phone.shelfsense.app and the PIN becomes the password. No email
 *   is ever sent, so "Confirm email" must be off in Supabase.)
 *   Without it: no cloud; the screen uses its on-phone PIN accounts instead.
 *
 * Edit config.js, not this file.
 */
(() => {
  const CFG = window.SHELFSENSE_CONFIG || {};

  // ?reset=1 wipes this phone's copy (handy before a demo)
  if (new URLSearchParams(location.search).get("reset") === "1") {
    try { Object.keys(localStorage).filter(k => k.startsWith("shelfsense")).forEach(k => localStorage.removeItem(k)); } catch (e) {}
    try { indexedDB.deleteDatabase("shelfsense-bills"); } catch (e) {}
    history.replaceState(null, "", location.pathname);
  }
  const ls = {
    get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
    set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };
  const reject = (code, message) => Promise.reject({code, message});
  const cloud = !!(CFG.supabaseUrl && CFG.supabaseAnonKey);
  const SB = cloud ? CFG.supabaseUrl.replace(/\/$/, "") : "";

  /* ------------------------------------------------------------ accounts (Supabase Auth) */
  const SESSION_KEY = "shelfsense-cloud-session", SHOPS_KEY = "shelfsense-cloud-shops", SHOP_KEY = "shelfsense-cloud-shop";
  let session = ls.get(SESSION_KEY);
  let refreshing = null;

  async function authCall(path, body, {method = "POST", token} = {}) {
    let res;
    try {
      res = await fetch(`${SB}/auth/v1/${path}`, {method,
        headers: {apikey: CFG.supabaseAnonKey, "Content-Type": "application/json",
                  ...(token ? {Authorization: "Bearer " + token} : {})},
        body: body ? JSON.stringify(body) : undefined});
    } catch (e) { throw {code: "offline", message: "No internet connection."}; }
    const text = await res.text();
    const json = text ? JSON.parse(text) : {};
    if (!res.ok) throw {code: "auth", status: res.status,
      message: json.msg || json.error_description || json.message || json.error || `error ${res.status}`};
    return json;
  }
  const DOMAIN = CFG.loginDomain || "phone.shelfsense.app";
  const loginName = mobile => `91${mobile}@${DOMAIN}`;
  // Supabase wants 6+ character passwords; a PIN is 4–6 digits, so it is padded the same way every time
  const loginSecret = (mobile, pin) => `ss-${pin}-${mobile.slice(-4)}`;
  function keep(s) {
    const meta = s.user.user_metadata || {};
    const mobile = meta.mobile || (s.user.email || "").replace(/^91/, "").split("@")[0];
    session = {access_token: s.access_token, refresh_token: s.refresh_token,
               expires_at: s.expires_at || Math.floor(Date.now() / 1000) + (s.expires_in || 3600),
               user: {id: s.user.id, mobile, name: meta.name || mobile}};
    ls.set(SESSION_KEY, session);
    return session;
  }
  async function refresh() {
    if (!session?.refresh_token) throw {code: "signed_out"};
    refreshing ||= authCall("token?grant_type=refresh_token", {refresh_token: session.refresh_token})
      .then(keep)
      .catch(e => { if (e.code === "auth") { session = null; ls.set(SESSION_KEY, null); window.dispatchEvent(new Event("shelfsense-signed-out")); } throw e; })
      .finally(() => { refreshing = null; });
    return refreshing;
  }
  async function token() {
    if (!session) throw {code: "signed_out"};
    if (session.expires_at - 60 < Date.now() / 1000 && navigator.onLine) await refresh();
    return session.access_token;
  }
  async function rest(path, {method = "GET", body, prefer, retried} = {}) {
    let res;
    try {
      res = await fetch(`${SB}/rest/v1/${path}`, {method,
        headers: {apikey: CFG.supabaseAnonKey, Authorization: "Bearer " + await token(),
                  "Content-Type": "application/json", ...(prefer ? {Prefer: prefer} : {})},
        body: body === undefined ? undefined : JSON.stringify(body)});
    } catch (e) {
      if (e?.code === "signed_out") throw {code: "unavailable", message: "signed out"};
      throw {code: "unavailable", message: "offline"};
    }
    if (res.status === 401 && !retried) {            // login expired: renew and try once more
      try { await refresh(); } catch (e) { throw {code: "unavailable", message: "please sign in again"}; }
      return rest(path, {method, body, prefer, retried: true});
    }
    const text = await res.text();
    const json = text ? JSON.parse(text) : null;
    if (res.status === 401) throw {code: "unavailable", message: "please sign in again"};
    if (res.status === 403) throw {code: "invalid_argument", message: json?.message || "not allowed"};
    if (!res.ok) throw {code: res.status >= 500 ? "unavailable" : "invalid_argument", message: json?.message || `error ${res.status}`};
    return json;
  }
  const niceError = e => ({code: e.code || "error", status: e.status, message: e.message || String(e)});

  const auth = {
    kind: "cloud",
    current() { return session ? {user: session.user, shop: ls.get(SHOP_KEY)} : null; },
    async signInMobile(mobile, pin) {
      try {
        keep(await authCall("token?grant_type=password", {email: loginName(mobile), password: loginSecret(mobile, pin)}));
        return this.current();
      } catch (e) { throw niceError(e); }
    },
    async signUpMobile(name, mobile, pin) {
      try {
        const r = await authCall("signup", {email: loginName(mobile), password: loginSecret(mobile, pin), data: {name, mobile}});
        if (r.access_token) { keep(r); return {signedIn: true}; }
        return {signedIn: false, needsConfirm: true};
      } catch (e) { throw niceError(e); }
    },
    async signOut() {
      const t = session?.access_token;
      session = null;
      [SESSION_KEY, SHOPS_KEY, SHOP_KEY].forEach(k => ls.set(k, null));
      if (t) authCall("logout", null, {token: t}).catch(() => {});
    },
    async setName(name) {
      try {
        await authCall("user", {data: {name, mobile: session.user.mobile}}, {method: "PUT", token: await token()});
        session.user.name = name; ls.set(SESSION_KEY, session);
        const shop = ls.get(SHOP_KEY);
        if (shop) await rest("rpc/set_display", {method: "POST", body: {p_shop: shop.id, p_display: name}});
      } catch (e) { throw niceError(e); }
    },
    async changePin(pin) {
      try { await authCall("user", {password: loginSecret(session.user.mobile, pin)}, {method: "PUT", token: await token()}); }
      catch (e) { throw niceError(e); }
    },
    async myShops() {
      try {
        const rows = await rest(`shop_members?select=role,display_name,shops(id,name,join_code)&user_id=eq.${session.user.id}`);
        const shops = rows.filter(r => r.shops).map(r => ({id: r.shops.id, name: r.shops.name, code: r.shops.join_code,
                                                            role: r.role, displayName: r.display_name}));
        ls.set(SHOPS_KEY, shops);
        return shops;
      } catch (e) {
        const cached = ls.get(SHOPS_KEY);
        if (cached) return cached;                 // offline: use what we knew
        throw niceError(e);
      }
    },
    useShop(shop) { ls.set(SHOP_KEY, shop); },
    async createShop(name) {
      try {
        const s = await rest("rpc/create_shop", {method: "POST", body: {p_name: name, p_display: session.user.name, p_mobile: session.user.mobile}});
        const shop = {id: s.id, name: s.name, code: s.join_code, role: "owner", displayName: session.user.name};
        this.useShop(shop);
        return shop;
      } catch (e) { throw niceError(e); }
    },
    async joinShop(code) {
      try {
        const s = await rest("rpc/join_shop", {method: "POST", body: {p_code: code, p_display: session.user.name, p_mobile: session.user.mobile}});
        const shop = {id: s.id, name: s.name, code: s.join_code, role: "staff", displayName: session.user.name};
        this.useShop(shop);
        return shop;
      } catch (e) { throw niceError(e); }
    },
    async members(shopId) {
      const rows = await rest(`shop_members?select=user_id,role,display_name,mobile,joined_at&shop_id=eq.${shopId}&order=joined_at`);
      return rows.map(r => ({id: r.user_id, name: r.display_name || "—", mobile: r.mobile || "", role: r.role, me: r.user_id === session.user.id}));
    },
    async removeMember(shopId, userId) { await rest("rpc/remove_member", {method: "POST", body: {p_shop: shopId, p_user: userId}}); },
    async newCode(shopId) { return rest("rpc/new_join_code", {method: "POST", body: {p_shop: shopId}}); },
    async renameShop(shopId, name) { await rest("rpc/rename_shop", {method: "POST", body: {p_shop: shopId, p_name: name}}); },
  };

  /* ------------------------------------------------------------ shop data (Supabase) */
  function supabaseDb() {
    const shopId = () => ls.get(SHOP_KEY)?.id;
    const full = p => `${shopId()}/${p}`;
    const snapDoc = (path, data) => ({id: path.split("/").pop(), exists: data != null, data: () => data,
                                      metadata: {fromCache: false, hasPendingWrites: false}});
    const subs = new Set();
    async function poll(s) {
      if (!session || !shopId()) return;
      try {
        if (s.kind === "doc") {
          const rows = await rest(`docs?path=eq.${encodeURIComponent(full(s.path))}&select=data`);
          s.next(snapDoc(s.path, rows?.[0]?.data ?? null));
        } else {
          const depth = full(s.path).split("/").length + 1;
          const rows = await rest(`docs?path=like.${encodeURIComponent(full(s.path) + "/*")}&data=not.is.null&select=path,data`);
          const docs = (rows || []).filter(r => r.path.split("/").length === depth)
            .map(r => ({id: r.path.split("/").pop(), exists: true, data: () => r.data, metadata: {}}));
          s.next({docs, size: docs.length, empty: !docs.length, docChanges: () => [], metadata: {}});
        }
      } catch (e) { /* offline or signed out: try again later */ }
    }
    const refreshAll = () => subs.forEach(poll);
    setInterval(() => { if (!document.hidden && navigator.onLine) refreshAll(); }, CFG.pollMs || 4000);
    window.addEventListener("online", refreshAll);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshAll(); });

    function doc(path) {
      return {
        path, id: path.split("/").pop(),
        async get() {
          const rows = await rest(`docs?path=eq.${encodeURIComponent(full(path))}&select=data`);
          return snapDoc(path, rows?.[0]?.data ?? null);
        },
        async set(data) {
          await rest("docs?on_conflict=path", {method: "POST", prefer: "resolution=merge-duplicates,return=minimal",
            body: {path: full(path), data, updated_at: new Date().toISOString()}});
          refreshAll();
        },
        async acquire({holder, ttlMs = 30000}) {
          const ok = await rest("rpc/acquire_lease", {method: "POST", body: {p_path: full(path), p_holder: holder, p_ttl_ms: ttlMs}});
          return {acquired: ok === true};
        },
        onSnapshot(next) { const s = {kind: "doc", path, next}; subs.add(s); poll(s); return () => subs.delete(s); },
        collection(sub) { return collection(`${path}/${sub}`); },
      };
    }
    function collection(path) {
      return {
        path, doc: id => doc(`${path}/${id}`),
        onSnapshot(next) { const s = {kind: "col", path, next}; subs.add(s); poll(s); return () => subs.delete(s); },
      };
    }
    return {doc, collection};
  }

  /* ------------------------------------------------------------ bill reader */
  async function shrink(file, maxSide = 1600) {
    const bmp = await createImageBitmap(file);
    const k = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise(r => c.toBlob(r, "image/jpeg", 0.85));
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return {media_type: "image/jpeg", data: btoa(bin)};
  }
  function parseJson(text) {
    try { return JSON.parse(text); } catch (e) {}
    const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/);
    if (fence) { try { return JSON.parse(fence[1]); } catch (e) {} }
    const a = text.search(/[\[{]/), b = Math.max(text.lastIndexOf("}"), text.lastIndexOf("]"));
    if (a >= 0 && b > a) { try { return JSON.parse(text.slice(a, b + 1)); } catch (e) {} }
    throw {code: "invalid_json", message: "reply was not JSON", text};
  }
  function billReader() {
    async function ask(prompt, opts = {}) {
      if (!navigator.onLine) return reject("upstream_error", "offline");
      const files = opts.images ? [...(opts.images.length !== undefined ? opts.images : [opts.images])] : [];
      const images = await Promise.all(files.slice(0, 5).map(f => shrink(f)));
      let res;
      try {
        res = await fetch(CFG.billReaderUrl, {method: "POST", signal: opts.signal,
          headers: {"Content-Type": "application/json", ...(CFG.appToken ? {"X-App-Token": CFG.appToken} : {})},
          body: JSON.stringify({prompt, images})});
      } catch (e) {
        return reject(e?.name === "AbortError" ? "cancelled" : "upstream_error", String(e));
      }
      if (res.status === 401 || res.status === 403) return reject("not_granted", "bill reader refused");
      if (res.status === 429) return reject("rate_limited", "too many requests");
      if (!res.ok) return reject("upstream_error", `bill reader ${res.status}`);
      const {text} = await res.json();
      if (!text) return reject("empty_completion", "no text");
      opts.onText?.({text, delta: text});
      return {text, truncated: false, modelTierApplied: "default"};
    }
    const sample = (prompt, opts) => ask(prompt, opts);
    sample.json = async (prompt, opts) => parseJson((await ask(prompt, opts)).text);
    sample.limits = async () => ({maxPromptBytes: 65536,
      images: {maxCount: 5, maxInputBytes: 20e6, mediaTypes: ["image/jpeg", "image/png", "image/webp"]}});
    return sample;
  }

  /* ------------------------------------------------------------ downloads */
  const downloads = {
    async save({filename, data}) {
      const blob = data instanceof Blob ? data : new Blob([data], {type: "application/json"});
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      return {status: "saved"};
    },
  };

  const services = {
    db: cloud ? supabaseDb() : null,
    sample: CFG.billReaderUrl ? billReader() : null,
    downloads,
  };
  window.claude = {use: async name => services[name] || null};
  if (cloud) window.SHELFSENSE_AUTH = auth;

  // installable + opens offline
  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
  }
})();
