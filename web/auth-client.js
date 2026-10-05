/* Supabase SDK owns session persistence, expiry and automatic token refresh. */
(function () {
  "use strict";
  var clientPromise;
  window.pondAuth = function () {
    if (!clientPromise) {
      clientPromise = (async function () {
        if (!window.supabase) throw new Error("The sign-in library could not load. Please reload.");
        var res = await fetch("/api/v1/public-config", {cache: "no-store"});
        var cfg = await res.json();
        if (!res.ok) throw new Error(cfg.detail || "Account setup is not complete.");
        return window.supabase.createClient(cfg.supabase_url, cfg.supabase_publishable_key, {
          auth: {persistSession: true, autoRefreshToken: true, detectSessionInUrl: true}
        });
      })();
    }
    return clientPromise;
  };
  window.pondToken = async function () {
    var client = await window.pondAuth();
    var result = await client.auth.getSession();
    if (result.error) throw result.error;
    var session = result.data.session;
    if (!session) {
      window.location.replace("/login");
      throw new Error("Sign in to access your ponds.");
    }
    return session.access_token;
  };
})();
