import { html, useState, useEffect, api, can } from "./lib.js";

const MODES = [
  ["sovereign-rules", "Sovereign · offline rule parser", "Deterministic, zero egress. Default for PSU / CVC deployments."],
  ["sovereign-local-llm", "Sovereign · on-prem open-weight LLM", "Llama/Mistral-class model served inside the firewall (loopback only unless explicitly allowed). Stricter sign-off threshold (0.90)."],
  ["cloud-llm", "Enterprise · cloud LLM", "Claude via api.anthropic.com. SKU-1 builds only - hard-locked off in SKU-2 Sovereign. Threshold 0.80."],
];

function Users() {
  const [list, setList] = useState([]);
  const [form, setForm] = useState({ username: "", pin: "", role: "operator" });
  const [msg, setMsg] = useState("");
  const load = () => api("/api/users").then(setList).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  async function create(e) {
    e.preventDefault();
    try { await api("/api/users", { method: "POST", body: form }); setMsg(`created ${form.username}`); setForm({ username: "", pin: "", role: "operator" }); load(); }
    catch (x) { setMsg(x.message); }
  }
  async function toggle(u) {
    try { await api(`/api/users/${u.username}/disabled`, { method: "POST", body: { disabled: !u.disabled } }); load(); }
    catch (x) { setMsg(x.message); }
  }
  return html`<section class="panel">
    <h2>Users & roles</h2>
    <table><thead><tr><th>user</th><th>role</th><th>state</th><th></th></tr></thead><tbody>
      ${list.map((u) => html`<tr key=${u.username}><td>${u.username}</td><td>${u.role}</td>
        <td>${u.disabled ? html`<span class="chip bad">disabled</span>` : u.locked ? html`<span class="chip warn">locked</span>` : html`<span class="chip ok">active</span>`}</td>
        <td class="r"><button class="btn small" onClick=${() => toggle(u)}>${u.disabled ? "Enable" : "Disable"}</button></td></tr>`)}
    </tbody></table>
    <form class="row" style=${{ marginTop: 12 }} onSubmit=${create}>
      <label class="sr-only" for="nu">new username</label>
      <input id="nu" type="text" placeholder="username" value=${form.username} onChange=${(e) => setForm({ ...form, username: e.target.value })} required />
      <label class="sr-only" for="np">new PIN</label>
      <input id="np" type="password" placeholder="PIN (6+)" value=${form.pin} onChange=${(e) => setForm({ ...form, pin: e.target.value })} required minlength="6" />
      <label class="sr-only" for="nr">role</label>
      <select id="nr" value=${form.role} onChange=${(e) => setForm({ ...form, role: e.target.value })}>
        <option value="operator">operator</option><option value="supervisor">supervisor</option><option value="admin">admin</option>
      </select>
      <button class="btn primary" type="submit">Create user</button>
    </form>
    <p class="sub">operator: propose, reject, approve non-safety items · supervisor: + safety sign-off, promote/retire, anchor audit · admin: + settings, users, audit export.</p>
    ${msg && html`<p class="sub">${msg}</p>`}
  </section>`;
}

function ChangePin() {
  const [f, setF] = useState({ old_pin: "", new_pin: "" });
  const [msg, setMsg] = useState("");
  async function go(e) {
    e.preventDefault();
    try { const r = await api("/api/users/me/pin", { method: "POST", body: f }); setMsg(r.detail); setTimeout(() => window.dispatchEvent(new Event("sov:logout")), 1200); }
    catch (x) { setMsg(x.message); }
  }
  return html`<section class="panel"><h2>Change my PIN</h2>
    <form class="row" onSubmit=${go}>
      <label class="sr-only" for="op">current PIN</label>
      <input id="op" type="password" placeholder="current PIN" value=${f.old_pin} onChange=${(e) => setF({ ...f, old_pin: e.target.value })} required />
      <label class="sr-only" for="nwp">new PIN</label>
      <input id="nwp" type="password" placeholder="new PIN (6+)" value=${f.new_pin} onChange=${(e) => setF({ ...f, new_pin: e.target.value })} required minlength="6" />
      <button class="btn" type="submit">Change PIN</button>
    </form>${msg && html`<p class="sub">${msg}</p>`}</section>`;
}

export function Sovereignty({ status, user, reload }) {
  const [err, setErr] = useState("");
  if (!status) return html`<div class="empty">Loading…</div>`;
  const s = status.settings;
  const admin = can(user, "admin");
  async function save(patch) {
    setErr("");
    try { await api("/api/settings", { method: "POST", body: patch }); reload(); } catch (e) { setErr(e.message); }
  }
  return html`<div class="grid">
    <div class="grid g2">
      <section class="panel">
        <h2>Three-tier sovereignty claim</h2>
        ${status.sovereignty.map((tr) => html`<div class="tier" key=${tr.tier}><div class="n">${tr.tier}</div><div>
          <div class="row"><b>${tr.name}</b><span class=${"chip " + (tr.state === "true today" ? "ok" : tr.state === "degraded" ? "bad" : "warn")}>${tr.state}</span></div>
          <div class="sub">${tr.detail}</div></div></div>`)}
        <p class="note" style=${{ marginTop: 12 }}>Say tier 3 out loud in the pitch: the GPU is imported. The CPU FP64 path is kept so export-control disruption degrades speed, not availability.</p>
      </section>
      <section class="panel">
        <h2>Deployment</h2>
        <dl class="kv">
          <dt>outbound egress</dt><dd>${status.egress}</dd>
          <dt>build SKU</dt><dd>${status.sku}</dd>
          <dt>solver</dt><dd>${status.solver_version}</dd>
          <dt>compute</dt><dd>${status.device.gpu_available ? `GPU ${status.device.gpu} (${status.device.vram_gb} GB), CUDA ${status.device.cuda_runtime}` : `CPU only (${status.device.cpu_threads} threads) - ${status.device.gpu_unavailable_reason}`}</dd>
          <dt>platform</dt><dd>${status.device.os} · Python ${status.device.python} · NumPy ${status.device.numpy}</dd>
          <dt>audit chain</dt><dd>${status.audit.ok ? `intact (${status.audit.entries} entries)` : "BROKEN"}</dd>
          <dt>baselines</dt><dd>HiGHS ${status.baselines.highs ? "✓" : "✗"} · Gurobi ${status.baselines.gurobi_cpu ? "✓" : "✗ (not installed)"}</dd>
          <dt>API docs</dt><dd><a href="/docs" target="_blank" rel="noopener">/docs (OpenAPI)</a> · <a href="/health" target="_blank" rel="noopener">/health</a> · <a href="/metrics" target="_blank" rel="noopener">/metrics</a></dd>
        </dl>
        <h3>NL compiler mode ${!admin && html`<span class="sub">(admin only)</span>`}</h3>
        <div class="radio" role="radiogroup" aria-label="NL compiler mode">${MODES.map(([k, name, d]) => {
          const locked = k === "cloud-llm" && !status.cloud_llm_allowed;
          return html`<label key=${k} class=${(s.nl_mode === k ? "on " : "") + (locked || !admin ? "locked" : "")}>
            <input type="radio" name="mode" checked=${s.nl_mode === k} disabled=${locked || !admin} onChange=${() => save({ nl_mode: k })} />
            <span><b>${name}</b>${locked && html` <span class="chip bad">locked in this build</span>`}<div class="sub">${d}</div></span></label>`;
        })}</div>
        <h3>Shadow-mode rollout</h3>
        <label class="chk"><input type="checkbox" checked=${s.shadow_mode} disabled=${!admin} onChange=${(e) => save({ shadow_mode: e.target.checked })} />
          New NL constraints run in the advisory shadow plan until a supervisor promotes them (recommended for the first operational quarter)</label>
        ${err && html`<p class="err">${err}</p>`}
      </section>
    </div>
    <div class="grid g2">
      ${admin ? html`<${Users} />` : html`<section class="panel"><h2>Users & roles</h2><p class="sub">Only an admin can manage users.</p></section>`}
      <${ChangePin} />
    </div>
  </div>`;
}
