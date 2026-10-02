import { html } from "./lib.js";

const API = "/api";

async function getJSON(path, options = {}) {
  const url = path === "/health"
    ? "/health"
    : API + path;

  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
  });

  if (!response.ok) {
    throw new Error(await response.text());
  }

  return response.json();
}

export function Sovereignty() {
  const loadStatus = async () => {
    const systemStatus = document.getElementById("system-status");
    const solverStatus = document.getElementById("solver-status");

    if (!systemStatus || !solverStatus) {
      return;
    }

    try {
      const status = await getJSON("/health");

      systemStatus.innerHTML = `
        <div class="status-row">
          <strong>Status</strong>
          <span>${status.status || "ok"}</span>
        </div>
      `;
    } catch (error) {
      systemStatus.innerHTML = `
        <p class="error">
          Unable to load system status.
        </p>
      `;
    }

    try {
      const solvers = await getJSON("/solvers");

      solverStatus.innerHTML = `
        <pre>${JSON.stringify(solvers, null, 2)}</pre>
      `;
    } catch (error) {
      solverStatus.innerHTML = `
        <p class="error">
          Unable to load solver status.
        </p>
      `;
    }
  };

  setTimeout(loadStatus, 0);

  return html`
    <div class="page sovereignty">
      <section class="panel">
        <div class="panel-head">
          <div>
            <h1>Sovereignty</h1>
            <p class="sub">
              System status, solver availability, and platform integrity.
            </p>
          </div>
        </div>

        <div class="grid g2">
          <section class="panel">
            <h2>System status</h2>

            <div id="system-status">
              <p class="sub">
                Loading system status...
              </p>
            </div>
          </section>

          <section class="panel">
            <h2>Solver availability</h2>

            <div id="solver-status">
              <p class="sub">
                Loading solver status...
              </p>
            </div>
          </section>
        </div>
      </section>
    </div>
  `;
}