/*
 * ShelfSense settings. Leave everything empty for a phone-only setup:
 * people sign up with a mobile number + PIN, and data stays on the phone.
 */
window.SHELFSENSE_CONFIG = {
  // Cloud accounts + multi-phone sync (optional) — Supabase project: Settings -> API
  supabaseUrl: "",          // e.g. "https://abcdxyz.supabase.co"
  supabaseAnonKey: "",      // the "anon public" key

  // Order confirmation links for wholesalers (optional) — needs the server function
  confirmUrl: "",           // e.g. "https://yoursite.netlify.app/api/confirm-order"

  // Find wholesalers nearby (optional) — Mappls / MapmyIndia
  placesUrl: "",            // best: "/api/places" (server function; keys stay on the server)
  mapplsKey: "",            // quick demo: a Mappls REST key used straight from the phone.
                            // Anyone who opens the site can read it, so restrict it to your
                            // domain in the Mappls console and keep it off a public site.

  // Bill-photo reading (optional) — needs the server function deployed (Vercel or Netlify)
  billReaderUrl: "",        // e.g. "/api/read-bill"
  appToken: "",             // must match APP_TOKEN on the server, if you set one

  pollMs: 4000,             // how often to check for changes from other phones
};
