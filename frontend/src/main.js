import { React, html, useState, useEffect, api, setCsrf, t, getLang, setLang } from "./lib.js";
import { Race } from "./race.js";
import { Plan } from "./plan.js";
import { Compiler } from "./compiler.js";
import { Audit } from "./audit.js";
import { Sovereignty } from "./sovereignty.js";
import { Twin } from "./twin.js";

const TABS = ["race", "twin", "plan", "compiler", "audit", "sovereignty"];

/** 3D perspective floor behind the dashboard (pure CSS; frozen under reduced motion). */
const Backdrop = () => html`<div class="backdrop" aria-hidden="true"><div class="floor"></div><div class="glow"></div></div>`;
const Logo = () => html`<i class="mark cube" aria-hidden="true"><b></b><b></b><b></b></i>`;

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try { localStorage.setItem("sov.theme", theme); } catch {}
}

function Login({ onLogin }) {
  const [username, setUser] = useState("");
  const [pin, setPin] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const u = await api("/api/auth/login", { method: "POST", body: { username, pin } });
      setCsrf(u.csrf);
      onLogin(u);
    } catch (x) { setErr(x.message); }
    setBusy(false);
  }
  return html`<main class="login-wrap"><${Backdrop} />
    <form class="panel login" onSubmit=${submit} aria-labelledby="login-title">
      <div class="brand"><${Logo} /><b>Sovereign Optimizer</b></div>
      <h1 id="login-title">${t("login.title")}</h1>
      <label for="u">${t("login.user")}</label>
      <input id="u" type="text" autocomplete="username" value=${username} onChange=${(e) => setUser(e.target.value)} required />
      <label for="p">${t("login.pin")}</label>
      <input id="p" type="password" autocomplete="current-password" value=${pin} onChange=${(e) => setPin(e.target.value)} required />
      ${err && html`<p class="err" role="alert">${err}</p>`}
      <button class="btn primary" type="submit" disabled=${busy}>${t("login.go")}</button>
      <p class="sub">First boot: the admin PIN is in <code>backend/data/BOOTSTRAP_ADMIN.txt</code> (and printed in the server console). Five wrong PINs lock the account for 15 minutes.</p>
    </form>
  </main>`;
}

function App() {
  const [user, setUser] = useState(undefined); // undefined = checking, null = signed out
  const [tab, setTab] = useState(() => {
    const h = location.hash.slice(1);
    return TABS.includes(h) ? h : "race";
  });
  const [status, setStatus] = useState(null);
  const [health, setHealth] = useState(null);
  const [offline, setOffline] = useState(false);
  const [pulse, setPulse] = useState(0);
  const auditN = React.useRef(null);
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem("sov.theme") || "dark"; } catch { return "dark"; } });
  const [, setLangState] = useState(getLang());

  useEffect(() => { applyTheme(theme); }, [theme]);
  useEffect(() => {
    api("/api/auth/me").then((u) => { setCsrf(u.csrf); setUser(u); }).catch(() => setUser(null));
    const out = () => { setUser(null); setCsrf(null); };
    window.addEventListener("sov:logout", out);
    return () => window.removeEventListener("sov:logout", out);
  }, []);
  const reload = () => api("/api/status").then(setStatus).catch(() => {});
  useEffect(() => { if (user) reload(); }, [tab, user]);
  useEffect(() => { history.replaceState(null, "", "#" + tab); }, [tab]);
  useEffect(() => {
    const on = () => { const h = location.hash.slice(1); if (TABS.includes(h)) setTab(h); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  // connectivity + health watch (audit #90): a visible "reconnecting" state instead of silent failure
  useEffect(() => {
    let stop = false;
    const poll = async () => {
      try {
        const r = await fetch("/health", { cache: "no-store" });
        const h = await r.json();
        setHealth(h);
        setOffline(false);
        // audit-chain pulse (dashboard plan feature 4): the badge flashes when a new entry is chained
        const n = h.audit && h.audit.entries;
        if (n != null && auditN.current != null && n > auditN.current) setPulse((p) => p + 1);
        if (n != null) auditN.current = n;
      } catch { setOffline(true); }
      if (!stop) setTimeout(poll, 5000);
    };
    poll();
    return () => { stop = true; };
  }, []);

  if (user === undefined) return html`<div class="empty">Loading…</div>`;
  if (user === null) return html`<${Login} onLogin=${setUser} />`;

  async function logout() {
    try { await api("/api/auth/logout", { method: "POST" }); } catch {}
    setCsrf(null); setUser(null);
  }

  return html`<div>
    <${Backdrop} />
    ${offline && html`<div class="conn-banner" role="status">${t("reconnecting")}</div>`}
    ${!offline && health && health.status !== "ok" && html`<div class="conn-banner warn" role="status">Service degraded: ${health.problems.join("; ")}</div>`}
    <header class="top">
      <div class="brand"><${Logo} /><b>Sovereign Optimizer</b><span>on-prem optimisation · ${status ? status.sku : ""}</span></div>
      <div class="badges">
        ${status && html`
          <span class=${"badge " + (status.air_gapped ? "ok" : "bad")}>${status.air_gapped ? "air-gapped · egress none" : "egress: cloud LLM"}</span>
          <span class=${"badge " + (status.device.gpu_available ? "ok" : "warn")} title=${status.device.gpu_unavailable_reason || ""}>${status.device.gpu_available ? "GPU " + status.device.gpu : "CPU fallback"}</span>
          <span key=${"a" + pulse} class=${"badge chain " + (status.audit.ok ? "ok" : "bad") + (pulse ? " pulse" : "")}
            title=${health && health.audit ? `${health.audit.entries} hash-chained entries` : ""}><i class="link"></i>${status.audit.ok ? "audit chain ok" : "audit chain BROKEN"}${health && health.audit ? ` · ${health.audit.entries}` : ""}</span>
          <span class=${"badge " + (status.settings.shadow_mode ? "ok" : "")}>${status.settings.shadow_mode ? "shadow mode on" : "shadow mode off"}</span>`}
        <span class="badge user" title="signed-in operator">${user.username} · ${user.role}</span>
        <label class="sr-only" for="lang">Language</label>
        <select id="lang" class="mini" value=${getLang()} onChange=${(e) => { setLang(e.target.value); setLangState(e.target.value); }}>
          <option value="en">EN</option><option value="hi">हिं</option>
        </select>
        <button class="btn small" onClick=${() => setTheme(theme === "hc" ? "dark" : "hc")} aria-pressed=${theme === "hc"}
          title="High-contrast control-room theme">${theme === "hc" ? "Standard theme" : "High contrast"}</button>
        <button class="btn small" onClick=${logout}>${t("logout")}</button>
      </div>
    </header>
    <nav class="tabs" role="tablist" aria-label="Sections">
      ${TABS.map((k) => html`<button key=${k} role="tab" aria-selected=${tab === k} class=${tab === k ? "on" : ""} onClick=${() => setTab(k)}>${t("tab." + k)}</button>`)}
    </nav>
    <main id="main">
      ${tab === "race" && html`<${Race} />`}
      ${tab === "twin" && html`<${Twin} />`}
      ${tab === "plan" && html`<${Plan} user=${user} />`}
      ${tab === "compiler" && html`<${Compiler} user=${user} status=${status} />`}
      ${tab === "audit" && html`<${Audit} user=${user} />`}
      ${tab === "sovereignty" && html`<${Sovereignty} status=${status} user=${user} reload=${reload} />`}
      <div class="foot">${t("advisory")}</div>
    </main>
  </div>`;
}

ReactDOM.createRoot(document.getElementById("root")).render(html`<${App} />`);
