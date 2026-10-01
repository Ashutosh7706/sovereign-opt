import { html, useState, useEffect, useRef, api, fmt, can, t } from "./lib.js";

const EXAMPLES = [
  "Tank 3 sulfur limit ≤ 0.2%",
  "Tank 3 sulfur limit 2.0",
  "Tank 3 sulphur must not exceed 2000 ppm",
  "FCC shutdown for maintenance",
  "diesel lifting at least 100 kbbl/d",
  "no more than 1 cargo of Basrah",
  "DHDS capacity max 70",
  "increase FCC capacity by 10 kbbl/d",
];
const MODE_LABEL = {
  "sovereign-rules": "Sovereign · offline rule parser",
  "sovereign-local-llm": "Sovereign · on-prem open-weight LLM",
  "cloud-llm": "Enterprise · cloud LLM (not air-gapped)",
};
// Draft -> Pending sign-off -> Approved -> Shadow/Production (audit #87)
export const BANNER = {
  auto_accepted: ["ok", "APPROVED by policy (no human needed)"],
  approved: ["ok", "APPROVED by a named person"],
  pending_signoff: ["warn", "PENDING SIGN-OFF - not applied to any plan yet"],
  blocked: ["bad", "BLOCKED by the validator - cannot be approved"],
  rejected: ["bad", "REJECTED - not applied"],
  unparsed: ["info", "NOT UNDERSTOOD - nothing applied"],
};
const UNDO_SECONDS = 5;

function Proposal({ p, user, onDecided }) {
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const [countdown, setCountdown] = useState(null); // {action, left}
  const timer = useRef(null);
  const [cls, label] = BANNER[p.status] || ["info", p.status];
  const needed = p.required_role || "operator";
  const allowed = can(user, needed);

  useEffect(() => () => clearInterval(timer.current), []);

  // confirm-with-timeout (audit #88): nothing reaches the server - or the audit log - until the
  // countdown expires, so an accidental click can be undone without leaving a trace.
  function arm(action) {
    setErr("");
    let left = UNDO_SECONDS;
    setCountdown({ action, left });
    clearInterval(timer.current);
    timer.current = setInterval(async () => {
      left -= 1;
      if (left > 0) { setCountdown({ action, left }); return; }
      clearInterval(timer.current);
      setCountdown(null);
      try { onDecided(await api(`/api/nl/proposals/${p.id}/decide`, { method: "POST", body: { action, note } })); }
      catch (e) { setErr(e.message); }
    }, 1000);
  }
  function undo() { clearInterval(timer.current); setCountdown(null); }

  const conf = p.confidence ?? 0;
  const confColor = conf >= (p.threshold ?? 0.9) ? "var(--ok)" : conf >= 0.6 ? "var(--warn)" : "var(--bad)";
  return html`<section class="panel" aria-live="polite">
    <div class=${"banner " + cls}>${label}${p.stage && html`<span class=${"chip " + (p.stage === "shadow" ? "info" : "accent")} style=${{ marginLeft: "auto" }}>in ${p.stage} plan</span>`}</div>
    <p class="quote" style=${{ marginTop: 12 }}>“${p.text}”</p>
    <p class="restate">${p.restatement}</p>
    ${p.llm_restatement && html`<p class="sub">LLM said: ${p.llm_restatement}</p>`}
    <p class="sub">Parsed by ${p.source} (<code>${p.parser}</code>) · by ${p.operator} at ${p.created}</p>
    ${(p.notes || []).filter((n) => n.startsWith("ALERT")).map((n, i) => html`<p key=${i} class="note warn">${n}</p>`)}
    ${p.sovereignty_relaxation && html`<p class="note warn">Sovereignty relaxation active: ${p.sovereignty_relaxation}</p>`}
    ${p.error && html`<p class="err">${p.error}</p>`}
    ${p.ir && html`
      <div class="grid g2" style=${{ marginTop: 10 }}>
        <div>
          <div class="row"><b>Confidence</b><span class="spacer"></span><span class="num">${conf.toFixed(2)}</span><span class="sub">threshold ${p.threshold?.toFixed(2)}</span></div>
          <div class="meter" role="meter" aria-valuemin="0" aria-valuemax="1" aria-valuenow=${conf} aria-label="confidence"><div class="v" style=${{ width: conf * 100 + "%", background: confColor }}></div><div class="th" style=${{ left: (p.threshold ?? 0.9) * 100 + "%" }}></div></div>
          ${p.penalties?.length > 0 && html`<ul class="sub">${p.penalties.map((x, i) => html`<li key=${i}>−${x.amount.toFixed(2)} ${x.reason}</li>`)}</ul>`}
          ${p.safety_tagged && html`<p><span class="chip warn">safety-tagged variable</span> <span class="sub">needs a supervisor's named sign-off</span></p>`}
          ${p.signoff_reasons?.length > 0 && html`<p class="sub">Sign-off because: ${p.signoff_reasons.join("; ")}</p>`}
        </div>
        <div>
          <b>Typed IR</b>
          <pre class="math">${JSON.stringify(Object.fromEntries(Object.entries(p.ir).filter(([, v]) => v !== null)), null, 1)}</pre>
        </div>
      </div>
      <h3>Symbolic validator</h3>
      <ul class="checks">${p.checks.map((c, i) => html`<li key=${i} class=${c.status}><span class="ic" aria-label=${c.status}>${c.status === "pass" ? "✓" : c.status === "warn" ? "!" : "✗"}</span><span>${c.check}</span><span>${c.detail}</span></li>`)}</ul>
      ${p.math?.length > 0 && html`<h3>Generated solver rows</h3><pre class="math">${p.math.join("\n")}</pre>`}
      ${p.impact && html`<p class="sub" style=${{ marginTop: 8 }}>Impact probe: margin ${fmt(p.impact.before.objective, 1)} → ${p.impact.after.objective != null ? fmt(p.impact.after.objective, 1) : p.impact.after.status} $k/d</p>`}
    `}
    ${p.status === "pending_signoff" && html`
      <h3>Sign-off <span class="sub">(requires ${needed}; you are ${user.username} · ${user.role})</span></h3>
      ${countdown ? html`<div class="banner warn" role="alert">
          ${countdown.action === "approve" ? "Approving" : "Rejecting"} in ${countdown.left} s - nothing is recorded until then.
          <button class="btn small" style=${{ marginLeft: "auto" }} onClick=${undo}>${t("undo")}</button></div>` : html`
      <div class="row">
        <label class="sr-only" for="note">Decision note</label>
        <input id="note" type="text" placeholder="note (e.g. confirmed with process-safety engineer)" style=${{ flex: 1, minWidth: 220 }} value=${note} onChange=${(e) => setNote(e.target.value)} />
        <button class="btn good" onClick=${() => arm("approve")} disabled=${!allowed} title=${allowed ? "" : `needs the ${needed} role`}>${t("approve")}</button>
        <button class="btn danger" onClick=${() => arm("reject")}>${t("reject")}</button>
      </div>
      ${!allowed && html`<p class="sub">Only a ${needed} (or admin) can approve this. You can still reject it.</p>`}`}
      ${err && html`<p class="err">${err}</p>`}`}
    ${p.decided_by && html`<p class="sub">Decision by ${p.decided_by}${p.decided_role ? ` (${p.decided_role})` : ""}${p.decision_note ? ": " + p.decision_note : ""}</p>`}
  </section>`;
}

export function Compiler({ user, status }) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [current, setCurrent] = useState(null);
  const [history, setHistory] = useState([]);
  const load = () => api("/api/nl/proposals").then(setHistory).catch(() => {});
  useEffect(() => { load(); }, []);
  const effMode = mode || status?.settings?.nl_mode || "sovereign-rules";

  async function propose() {
    if (!text.trim()) return;
    setBusy(true); setErr("");
    try { setCurrent(await api("/api/nl/propose", { method: "POST", body: { text, mode: effMode } })); load(); }
    catch (e) { setErr(e.message); }
    setBusy(false);
  }

  return html`<div class="grid g-side">
    <div class="grid">
      <section class="panel">
        <h2>Plain-English constraint compiler</h2>
        <label class="sr-only" for="nl">Constraint in plain English</label>
        <textarea id="nl" rows="2" maxlength="500" value=${text} placeholder="e.g. Tank 3 sulfur limit ≤ 0.2%" onChange=${(e) => setText(e.target.value)}
          onKeyDown=${(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) propose(); }}></textarea>
        <div class="examples">${EXAMPLES.map((x) => html`<button key=${x} onClick=${() => setText(x)}>${x}</button>`)}</div>
        <div class="row" style=${{ marginTop: 12 }}>
          <select value=${effMode} onChange=${(e) => setMode(e.target.value)} aria-label="parser mode">
            ${Object.entries(MODE_LABEL).map(([k, v]) => html`<option key=${k} value=${k} disabled=${k === "cloud-llm" && !status?.cloud_llm_allowed}>${v}${k === "cloud-llm" && !status?.cloud_llm_allowed ? " - locked in SKU-2" : ""}</option>`)}
          </select>
          <span class="spacer"></span>
          <button class="btn primary" onClick=${propose} disabled=${busy || !text.trim()}>${busy ? "Compiling + probing…" : t("compile")}</button>
        </div>
        ${effMode === "cloud-llm" && html`<p class="note warn" style=${{ marginTop: 10 }}>Cloud mode sends this sentence and the plant vocabulary to api.anthropic.com. Not permitted in a CVC air-gapped deployment.</p>`}
        ${err && html`<p class="err">${err}</p>`}
      </section>
      ${current && html`<${Proposal} key=${current.id + current.status} p=${current} user=${user} onDecided=${(p) => { setCurrent(p); load(); }} />`}
    </div>
    <section class="panel">
      <h2>Recent proposals</h2>
      ${history.length === 0 ? html`<div class="empty">Nothing yet</div>` : html`
      <table><tbody>${history.map((h) => html`<tr key=${h.id} class="clickable" tabIndex="0" onClick=${() => setCurrent(h)} onKeyDown=${(e) => e.key === "Enter" && setCurrent(h)}>
        <td><span class=${"chip " + (BANNER[h.status]?.[0] || "")}>${h.status.replace(/_/g, " ")}</span>${h.stage ? html` <span class="chip info">${h.stage}</span>` : ""}</td>
        <td>${h.text}<div class="sub">${h.operator} · conf ${(h.confidence ?? 0).toFixed(2)}</div></td></tr>`)}</tbody></table>`}
      <p class="sub" style=${{ marginTop: 12 }}>Policy: parses below the mode threshold, any validator warning, or wording aimed at the system need a named sign-off. Safety-tagged variables (sulfur, pressure, temperature) need a <b>supervisor</b>. Accepted constraints enter the <b>shadow</b> plan first while shadow mode is on; promotion to production also needs a supervisor.</p>
    </section>
  </div>`;
}
