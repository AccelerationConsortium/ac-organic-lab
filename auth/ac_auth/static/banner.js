/* SDL2 Lab — shared auth banner, served by ac_auth (GET /auth/banner.js).
 *
 * ONE line opts a UI in:  <script src="/auth/banner.js" defer></script>
 *
 * The banner's markup, styling, and login/logout logic all live here, so every
 * UI behind the single edge (dashboard at /, xArm at /xarm5/web/, BitacoraDB
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
    // Light theme. Everything lives inside a shadow root, so page styles don't
    // leak in (and ours don't leak out). We still set font/color explicitly
    // because those few properties inherit across the shadow boundary. No
    // :host{all:initial} — it would fight a host slot's positioning classes.
    "*{box-sizing:border-box;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}" +
    ".bar{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem .6rem;min-height:44px;padding:.35rem 1rem;" +
    "background:#ffffff;color:#0f172a;border-bottom:1px solid #e2e8f0;font-size:14px;" +
    "box-shadow:0 1px 2px rgba(15,23,42,.04)}" +
    ".brand{font-weight:600;letter-spacing:.02em;color:#0f172a;margin-right:.4rem}" +
    ".dot{width:8px;height:8px;border-radius:50%;background:#cbd5e1;display:inline-block}" +
    ".dot.on{background:#16a34a;box-shadow:0 0 0 3px rgba(22,163,74,.15)}" +
    ".spacer{flex:1 1 auto}" +
    ".avatar{width:26px;height:26px;border-radius:50%;background:#e2e8f0;color:#0f172a;" +
    "display:flex;align-items:center;justify-content:center;font-weight:600;font-size:13px}" +
    ".who{display:flex;flex-direction:column;line-height:1.15;text-align:right}" +
    ".who .email{color:#0f172a}.who .role{color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.04em}" +
    // font-family:inherit (not the font: shorthand) so form controls pick up
    // the banner's own sans stack from the * rule, never the host page's font.
    "button{font-family:inherit;font-size:inherit;border-radius:7px;border:1px solid #cbd5e1;background:#ffffff;color:#0f172a;" +
    "padding:.3rem .7rem;cursor:pointer;white-space:nowrap}button:hover{background:#f1f5f9}button:disabled{opacity:.5;cursor:default}" +
    "button.primary{background:#2563eb;border-color:#2563eb;color:#fff}button.primary:hover{background:#1d4ed8}" +
    "button.ghost{background:transparent;color:#475569}button.ghost:hover{background:#f1f5f9}" +
    "select,input{font-family:inherit;font-size:inherit;border-radius:7px;border:1px solid #cbd5e1;background:#ffffff;color:#0f172a;padding:.3rem .5rem;min-width:0}" +
    "select{max-width:13rem}" +
    // 6-digit code entry: monospace + tabular digits so typed characters space
    // evenly; 16px stops iOS Safari from zoom-jumping when the field focuses.
    "input.code{width:7.5rem;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;" +
    "font-size:16px;font-variant-numeric:tabular-nums;letter-spacing:.18em;text-align:center;padding:.25rem .4rem}" +
    "input.code::placeholder{font-family:ui-sans-serif,system-ui,sans-serif;font-size:14px;letter-spacing:normal;color:#94a3b8}" +
    ".msg{color:#2563eb;font-size:12px}.msg.err{color:#dc2626}" +
    ".hide{display:none!important}" +
    // Narrow screens: tighten padding, cap wide items, and drop the status
    // message onto its own full-width row instead of squeezing the bar.
    "@media (max-width:640px){" +
    ".bar{padding:.35rem .6rem}" +
    "select{max-width:40vw}" +
    ".who .email{max-width:38vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" +
    ".msg{flex-basis:100%;order:10;text-align:right}" +
    "}" +
    // Dark theme — toggled via the .theme-toggle button below. Applied as a
    // ".dark" class on .bar (this shadow root's own styling) in lockstep with
    // a "dark" class on the HOST page's <html>, so every UI's own Tailwind
    // dark: variants (or equivalent) switch together with the banner.
    ".bar.dark{background:#0b1120;color:#e2e8f0;border-bottom-color:#1e293b;box-shadow:0 1px 2px rgba(0,0,0,.3)}" +
    ".bar.dark .brand{color:#e2e8f0}" +
    ".bar.dark .dot{background:#334155}" +
    ".bar.dark .dot.on{background:#22c55e;box-shadow:0 0 0 3px rgba(34,197,94,.15)}" +
    ".bar.dark .avatar{background:#1e293b;color:#e2e8f0}" +
    ".bar.dark .who .email{color:#e2e8f0}.bar.dark .who .role{color:#94a3b8}" +
    ".bar.dark button{background:#0f172a;color:#e2e8f0;border-color:#334155}" +
    ".bar.dark button:hover{background:#1e293b}" +
    ".bar.dark button.primary{background:#2563eb;border-color:#2563eb;color:#fff}" +
    ".bar.dark button.primary:hover{background:#1d4ed8}" +
    ".bar.dark button.ghost{color:#94a3b8}.bar.dark button.ghost:hover{background:#1e293b}" +
    ".bar.dark select,.bar.dark input{background:#0f172a;color:#e2e8f0;border-color:#334155}" +
    ".bar.dark input.code::placeholder{color:#64748b}" +
    ".bar.dark .msg{color:#60a5fa}.bar.dark .msg.err{color:#f87171}" +
    // Theme toggle: an inline line icon (sun = "switch to light", moon =
    // "switch to dark") drawn in currentColor, so it follows the ghost-button
    // colour in both themes. Sized like the text beside it.
    ".theme-toggle{padding:.3rem .45rem;line-height:0;display:inline-flex;align-items:center}" +
    ".theme-toggle svg{width:16px;height:16px;display:block}";

  // Icons for the theme toggle (24-unit viewBox, 2px round strokes).
  var ICON_SUN =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="4"/>' +
    '<path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>' +
    '</svg>';
  var ICON_MOON =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>' +
    '</svg>';

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

  // Framed inside another lab UI (dashboard /workflows framing Bitácora,
  // /utils/xarm_control framing the xArm panel, …): the HOST page already
  // renders this banner, so a second bar inside the frame is pure noise.
  // Go headless instead of returning outright — panels still consume the
  // window.labAuth identity contract (claim release, gating), and the theme
  // must stay in lockstep with the host, which shares our localStorage.
  var FRAMED = (function () {
    try { return window.self !== window.top; } catch (e) { return true; }
  })();

  function headless() {
    function systemPrefersDark() {
      try { return window.matchMedia("(prefers-color-scheme: dark)").matches; } catch (e) { return false; }
    }
    function applyStoredTheme(stored) {
      var dark = stored === "dark" || stored === "light" ? stored === "dark" : systemPrefersDark();
      try { document.documentElement.classList.toggle("dark", dark); } catch (e) { /* no-op */ }
    }
    try { applyStoredTheme(localStorage.getItem("theme")); } catch (e) { applyStoredTheme(null); }
    // The host banner's theme toggle writes localStorage("theme"); the
    // storage event fires in this frame (same origin, different browsing
    // context), so the framed UI follows the host's toggle live.
    window.addEventListener("storage", function (e) {
      if (e.key === "theme") applyStoredTheme(e.newValue);
    });
    jget(A.me).then(function (r) {
      var id = r.data && r.data.authenticated && r.data.identity ? r.data.identity : null;
      setIdentity(id);
    }).catch(function () { setIdentity(null); });
  }

  function mount() {
    if (FRAMED) { headless(); return; }
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
    var themeBtn = el("button", { class: "ghost theme-toggle", type: "button" });

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
    var code = el("input", { class: "code hide", placeholder: "code", inputmode: "numeric", maxlength: "12", autocomplete: "one-time-code" });
    var verifyBtn = el("button", { class: "primary hide", text: "Log in" });
    var login = el("div", {}, [sel, sendBtn, code, verifyBtn]);
    login.style.cssText = "display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:.4rem;min-width:0";
    var signedOut = el("div", { class: "hide" }, [login]);

    var msg = el("span", { class: "msg" });
    // Brand on the left; account + buttons pushed to the right by the spacer.
    var bar = el("div", { class: "bar" }, [dot, brand, themeBtn, el("span", { class: "spacer" }), msg, signedOut, signedIn]);
    root.appendChild(bar);

    // Dark mode — one toggle shared by every UI behind the single edge.
    // Source of truth is localStorage("theme"); we also mirror it onto the
    // HOST page's <html> so each UI's own dark: styling (Tailwind or
    // otherwise) switches in lockstep with the banner. A page that sets its
    // own "dark" class before this script runs (e.g. a blocking inline
    // script, to avoid a flash of the wrong theme) is respected as-is here —
    // applyTheme below is idempotent either way.
    function storedTheme() {
      try {
        var v = localStorage.getItem("theme");
        return v === "dark" || v === "light" ? v : null;
      } catch (e) { return null; }
    }
    function systemPrefersDark() {
      try { return window.matchMedia("(prefers-color-scheme: dark)").matches; } catch (e) { return false; }
    }
    var isDark = storedTheme() ? storedTheme() === "dark" : systemPrefersDark();

    function applyTheme(dark) {
      isDark = dark;
      bar.classList.toggle("dark", dark);
      // Static markup, no user data — safe to set as HTML.
      themeBtn.innerHTML = dark ? ICON_SUN : ICON_MOON;
      themeBtn.title = dark ? "Switch to light theme" : "Switch to dark theme";
      themeBtn.setAttribute("aria-label", themeBtn.title);
      try { document.documentElement.classList.toggle("dark", dark); } catch (e) { /* no-op */ }
    }
    applyTheme(isDark);

    themeBtn.addEventListener("click", function () {
      var next = !isDark;
      try { localStorage.setItem("theme", next ? "dark" : "light"); } catch (e) { /* no-op */ }
      applyTheme(next);
    });

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
