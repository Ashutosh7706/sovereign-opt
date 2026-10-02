import { React, html, useState, useEffect, api, t, getLang, setLang } from "./lib.js";
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

function App() {
  const user = { username: "guest", role: "supervisor" };

  const [tab, setTab] = useState(() => {
    const h = location.hash.slice(1);
    return TABS.includes(h) ? h : "race";
  });

  const [status, setStatus] = useState(null);
  const [health, setHealth] = useState(null);
  const [offline, setOffline] = useState(false);
  const [pulse, setPulse] = useState(0);
  const auditN = React.useRef(null);

  useEffect(() => {
    const onHash = () => {
      const h = location.hash.slice(1);
      if (TABS.includes(h)) setTab(h);
    };

    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    try {
      const saved = localStorage.getItem("sov.theme");
      applyTheme(saved || "dark");
    } catch {
      applyTheme("dark");
    }
  }, []);

  useEffect(() => {
    let alive = true;

    const checkHealth = async () => {
      try {
        const r = await api("/api/health");
        if (!alive) return;

        setHealth(r);
        setOffline(false);
      } catch {
        if (!alive) return;
        setOffline(true);
      }
    };

    checkHealth();
    const timer = setInterval(checkHealth, 10000);

    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const timer = setInterval(() => {
      setPulse((p) => p + 1);
    }, 5000);

    return () => clearInterval(timer);
  }, []);

  function reload() {
    window.location.reload();
  }

  function setTheme(theme) {
    applyTheme(theme);
  }

  const theme = document.documentElement.getAttribute("data-theme") || "dark";

  return html`
    <div class="app">
      <${Backdrop} />

      <header class="topbar">
        <div class="brand">
          <${Logo} />
          <div>
            <div class="brand-title">Sovereign Optimizer</div>
            <div class="brand-subtitle">Decision Intelligence Platform</div>
          </div>
        </div>

        <div class="top-actions">
          ${offline
            ? html`<span class="status offline">Offline</span>`
            : html`<span class="status online">System Online</span>`}

          <button
            class="btn small"
            onClick=${() => setTheme(theme === "hc" ? "dark" : "hc")}
            aria-pressed=${theme === "hc"}
            title="High-contrast control-room theme"
          >
            ${theme === "hc" ? "Standard theme" : "High contrast"}
          </button>
        </div>
      </header>

      <nav class="tabs" role="tablist" aria-label="Sections">
        ${TABS.map(
          (k) =>
            html`
              <button
                key=${k}
                role="tab"
                aria-selected=${tab === k}
                class=${tab === k ? "on" : ""}
                onClick=${() => {
                  setTab(k);
                  location.hash = k;
                }}
              >
                ${t("tab." + k)}
              </button>
            `
        )}
      </nav>

      <main id="main">
        ${tab === "race" && html`<${Race} />`}
        ${tab === "twin" && html`<${Twin} />`}
        ${tab === "plan" && html`<${Plan} user=${user} />`}
        ${tab === "compiler" &&
          html`<${Compiler} user=${user} status=${status} />`}
        ${tab === "audit" && html`<${Audit} user=${user} />`}
        ${tab === "sovereignty" &&
          html`
            <${Sovereignty}
              status=${status}
              user=${user}
              reload=${reload}
            />
          `}

        <div class="foot">${t("advisory")}</div>
      </main>
    </div>
  `;
}

ReactDOM.createRoot(document.getElementById("root")).render(
  html`<${App} />`
);