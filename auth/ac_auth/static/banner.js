/* SDL2 Lab — shared auth banner, served by ac_auth (GET /auth/banner.js).
 *
 * ONE line opts a UI in:  <script src="/auth/banner.js" defer></script>
 *
 * The banner's markup, styling, and login/logout logic all live here, so every
 * UI behind the single edge (dashboard at /, xArm at /xarm5/web/, AnaliticaDB
 * at /analytica/, …) gets the same top bar and updating it once updates them
 * all. It talks only to same-origin /auth/* endpoints, so the host-only
 * ac_auth_session cookie rides along automatically — no per-UI auth code.
 *
 * Host-page contract (so panels can react to identity):
 *   window.labAuth = { enabled: true, identity: {email, role} | null }
 *   document 'labauth:change' event fires with the identity (or null) on change
 *   window.labAuth.releaseClaimOnSignOut  — optional hook a panel sets; the
 *     banner awaits it before logging out (xArm uses it to drop its claim).
 * This matches the contract the xArm panel's main.js already consumes, so the
 * bespoke per-panel banner can be replaced by this one with no logic changes.
 */
(function () {
  "use strict";
  if (window.__acAuthBanner) return;            // idempotent (double-include safe)
  window.__acAuthBanner = true;

  var A = {
    me: "/auth/me",
    users: "/auth/users",
    login: "/auth/login",
    verify: "/auth/verify-code",
    logout: "/auth/logout",
  };

  window.labAuth = window.labAuth || { enabled: true, identity: null };
  window.labAuth.enabled = true;

  function setIdentity(identity) {
    window.labAuth.identity = identity;
    try {
      document.dispatchEvent(new CustomEvent("labauth:change", { detail: identity }));
    } catch (e) { /* no-op */ }
  }

  async function jget(url) {
    var r = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
    return { ok: r.ok, status: r.status, data: await r.json().catch(function () { return {}; }) };
  }
  async function jpost(url, body) {
    var r = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return { ok: r.ok, status: r.status, data: await r.json().catch(function () { return {}; }) };
  }

  var CSS =
    // Everything lives inside a shadow root, so page styles don't leak in (and
    // ours don't leak out). We still set font/color explicitly because those
    // few properties inherit across the shadow boundary. No :host{all:initial}
    // — it would fight a host slot's own positioning classes on the dashboard.
    "*{box-sizing:border-box;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}" +
    ".bar{display:flex;align-items:center;gap:.6rem;min-height:44px;padding:.35rem .8rem;" +
    "background:#0f172a;color:#e2e8f0;border-bottom:1px solid #1e293b;font-size:14px}" +
    ".brand{font-weight:600;letter-spacing:.02em;color:#f8fafc;margin-right:.4rem}" +
    ".dot{width:8px;height:8px;border-radius:50%;background:#475569;display:inline-block}" +
    ".dot.on{background:#34d399;box-shadow:0 0 0 3px rgba(52,211,153,.18)}" +
    ".spacer{flex:1 1 auto}" +
    ".avatar{width:26px;height:26px;border-radius:50%;background:#334155;color:#f1f5f9;" +
    "display:flex;align-items:center;justify-content:center;font-weight:600;font-size:13px}" +
    ".who{display:flex;flex-direction:column;line-height:1.15}" +
    ".who .email{color:#f1f5f9}.who .role{color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:.04em}" +
    "button{font:inherit;border-radius:7px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;" +
    "padding:.3rem .7rem;cursor:pointer}button:hover{background:#334155}button:disabled{opacity:.5;cursor:default}" +
    "button.primary{background:#2563eb;border-color:#2563eb;color:#fff}button.primary:hover{background:#1d4ed8}" +
    "button.ghost{background:transparent}" +
    "select,input{font:inherit;border-radius:7px;border:1px solid #334155;background:#0b1220;color:#e2e8f0;padding:.3rem .5rem}" +
    "input{width:7.5rem;letter-spacing:.2em}" +
    ".msg{color:#93c5fd;font-size:12px}.msg.err{color:#fca5a5}" +
    ".hide{display:none!important}";

  function el(tag, props, kids) {
    var e = document.createElement(tag);
    if (props) Object.keys(props).forEach(function (k) {
      if (k === "class") e.className = props[k];
      else if (k === "text") e.textContent = props[k];
      else e.setAttribute(k, props[k]);
    });
    (kids || []).forEach(function (c) { e.appendChild(c); });
    return e;
  }

  function mount() {
    if (!document.body) { document.addEventListener("DOMContentLoaded", mount); return; }
    // Prefer a host-provided slot (e.g. the dashboard renders
    // <div id="ac-auth-banner-slot">) so React owns the light-DOM element and
    // the banner lives in its shadow root — no hydration conflict. On a plain
    // static page (xArm panel) no slot exists, so create + prepend our own.
    var host = document.getElementById("ac-auth-banner-slot");
    if (host) {
      if (host.shadowRoot) return;              // already mounted
    } else {
      host = document.createElement("div");
      host.id = "ac-auth-banner";
      host.style.cssText = "position:sticky;top:0;left:0;right:0;z-index:2147483000";
      document.body.insertBefore(host, document.body.firstChild);
    }
    var root = host.attachShadow({ mode: "open" });
    root.appendChild(el("style", { text: CSS }));

    var dot = el("span", { class: "dot" });
    var brand = el("span", { class: "brand", text: "SDL2 Lab" });

    // signed-in cluster
    var avatar = el("div", { class: "avatar", text: "?" });
    var email = el("span", { class: "email" });
    var role = el("span", { class: "role" });
    var who = el("div", { class: "who" }, [email, role]);
    var signout = el("button", { class: "ghost", text: "Sign out" });
    var signedIn = el("div", { class: "bar-cluster hide" }, [avatar, who, signout]);
    signedIn.style.cssText = "display:flex;align-items:center;gap:.5rem";

    // signed-out cluster (login)
    var sel = el("select");
    var sendBtn = el("button", { class: "primary", text: "Send code" });
    var code = el("input", { class: "hide", placeholder: "code", inputmode: "numeric", maxlength: "12" });
    var verifyBtn = el("button", { class: "primary hide", text: "Log in" });
    var login = el("div", {}, [sel, sendBtn, code, verifyBtn]);
    login.style.cssText = "display:flex;align-items:center;gap:.4rem";
    var signedOut = el("div", { class: "hide" }, [login]);

    var msg = el("span", { class: "msg" });
    var bar = el("div", { class: "bar" }, [dot, brand, signedOut, signedIn, el("span", { class: "spacer" }), msg]);
    root.appendChild(bar);

    function setMsg(t, isErr) { msg.textContent = t || ""; msg.className = "msg" + (isErr ? " err" : ""); }

    function showSignedIn(id) {
      dot.classList.add("on");
      avatar.textContent = (id.email || "?").charAt(0).toUpperCase();
      email.textContent = id.email;
      role.textContent = id.role || "user";
      signedIn.classList.remove("hide");
      signedOut.classList.add("hide");
      setMsg("");
    }
    function showSignedOut() {
      dot.classList.remove("on");
      signedIn.classList.add("hide");
      signedOut.classList.remove("hide");
      code.classList.add("hide"); verifyBtn.classList.add("hide");
      code.value = ""; sendBtn.disabled = false; sendBtn.textContent = "Send code";
    }

    async function loadUsers() {
      sel.innerHTML = "";
      var r = await jget(A.users);
      var users = (r.data && r.data.users) || [];
      var ph = el("option", { value: "", text: "Select your name…" });
      sel.appendChild(ph);
      users.forEach(function (u) { sel.appendChild(el("option", { value: u.id, text: u.name })); });
    }

    async function refresh() {
      try {
        var r = await jget(A.me);
        if (r.data && r.data.authenticated && r.data.identity) {
          setIdentity(r.data.identity);
          showSignedIn(r.data.identity);
        } else {
          setIdentity(null);
          showSignedOut();
          await loadUsers();
        }
      } catch (e) {
        setIdentity(null);
        showSignedOut();
        setMsg("Auth service unreachable.", true);
      }
    }

    sendBtn.addEventListener("click", async function () {
      var id = sel.value;
      if (!id) { setMsg("Pick your name first.", true); return; }
      sendBtn.disabled = true; setMsg("Sending…");
      var r = await jpost(A.login, { id: id });
      sendBtn.disabled = false; sendBtn.textContent = "Resend";
      if (r.ok) {
        setMsg("Code emailed — check your inbox.");
        code.classList.remove("hide"); verifyBtn.classList.remove("hide"); code.focus();
      } else {
        setMsg((r.data && r.data.detail) || ("Failed (HTTP " + r.status + ")."), true);
      }
    });

    async function doVerify() {
      var id = sel.value, c = (code.value || "").trim();
      if (!id || !c) return;
      verifyBtn.disabled = true;
      var r = await jpost(A.verify, { id: id, code: c });
      verifyBtn.disabled = false;
      if (r.ok) {
        // Full reload so every UI on the page (e.g. the dashboard's own
        // control-tile gating, which reads the session independently) picks
        // up the new session, not just this banner.
        window.location.reload();
      } else {
        setMsg((r.data && r.data.detail) || "Invalid or expired code.", true);
      }
    }
    verifyBtn.addEventListener("click", doVerify);
    code.addEventListener("keydown", function (e) { if (e.key === "Enter") doVerify(); });

    signout.addEventListener("click", async function () {
      // Let a panel relinquish its claim before the session ends (xArm sets this).
      try {
        if (typeof window.labAuth.releaseClaimOnSignOut === "function") {
          await window.labAuth.releaseClaimOnSignOut();
        }
      } catch (e) { /* best-effort */ }
      try { await jpost(A.logout, {}); } catch (e) { /* cookie clear is server-side */ }
      setIdentity(null);
      // Reload so gated pages (e.g. /xarm5) re-gate and bounce to login.
      window.location.reload();
    });

    refresh();
  }

  mount();
})();
