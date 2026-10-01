import { html, useState, useEffect, api, fmt, short, can } from "./lib.js";

function summary(e) {
  const p = e.payload || {};
  switch (e.event) {
    case "nl.proposed": return `“${p.text}” → ${p.status} (conf ${p.confidence})`;
    case "nl.accepted": return `constraint ${p.constraint} active in ${p.stage}: “${p.text}”`;
    case "nl.approved": return `approved proposal ${p.proposal}${p.note ? " - " + p.note : ""}`;
    case "nl.rejected": return `rejected proposal ${p.proposal}${p.note ? " - " + p.note : ""}`;
    case "nl.blocked": return `validator blocked ${p.proposal}`;
    case "nl.unparsed": return `“${p.text}” not understood`;
    case "constraint.promoted": return `promoted ${p.constraint} to production`;
    case "constraint.retired": return `retired ${p.constraint}`;
    case "constraint.superseded": return `${p.constraint} superseded by ${p.by}`;
    case "plan.solved": return `production ${p.production?.status} ${fmt(p.production?.objective, 1)}${p.shadow ? ` · shadow ${p.shadow.status} ${fmt(p.shadow.objective, 1)}` : ""}`;
    case "race.completed": return `${p.model} · replay ${p.replay}`;
    case "replay.run": return `${p.bundle}: ${p.reason}`;
    case "settings.changed": return `nl_mode=${p.new?.nl_mode}, shadow_mode=${p.new?.shadow_mode}`;
    case "auth.login": return `signed in (${p.role}) from ${p.ip}`;
    case "auth.logout": return "signed out";
    case "auth.bootstrap": return `first boot: created ${p.user}`;
    case "ops.backup": return `backup ${p.file}`;
    case "service.stopped": return `stopped (in-flight abandoned: ${p.inflight_abandoned})`;
    case "auth.login_failed": return `FAILED sign-in from ${p.ip}: ${p.reason}`;
    case "auth.user_created": return `created ${p.username} (${p.role})`;
    case "audit.anchored": return `chain tip seq ${p.seq} anchored to ${p.destination}`;
    case "alert.solver_failures": return `ALERT: ${p.consecutive_failures} consecutive solver failures`;
    case "nl.llm_unavailable": return `ALERT: ${p.mode} unavailable (${p.consecutive_failures}x) - offline parser used`;
    case "sovereignty.relaxed": return `SOVEREIGNTY RELAXED: ${p.flag} - ${p.effect}`;
    case "service.started": return `started ${p.version} · ${p.sku}`;
    case "model.uploaded": return `${p.name} (${p.m}×${p.n})`;
    default: return JSON.stringify(p).slice(0, 120);
  }
}

export function Audit({ user }) {
  const [data, setData] = useState(null);
  const [bundles, setBundles] = useState([]);
  const [replay, setReplay] = useState({});
  const [err, setErr] = useState("");
  const load = () => Promise.all([api("/api/audit").then(setData), api("/api/replays").then(setBundles)]).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  async function run(id) {
    setReplay((r) => ({ ...r, [id]: { busy: true } }));
    try { const res = await api(`/api/replays/${id}/replay`, { method: "POST" }); setReplay((r) => ({ ...r, [id]: res })); load(); }
    catch (e) { setReplay((r) => ({ ...r, [id]: { ok: false, reason: e.message } })); }
  }

  const [msg, setMsg] = useState("");
  async function anchor() {
    try { const a = await api("/api/audit/anchor", { method: "POST" }); setMsg(`Anchored seq ${a.seq} (${short(a.hash, 16)}…) to the external anchor directory.`); load(); }
    catch (e) { setMsg(e.message); }
  }
  const v = data?.verify;
  return html`<div class="grid">
    <section class="panel">
      <div class="row"><h2 style=${{ margin: 0 }}>Immutable audit log</h2><span class="spacer"></span>
        ${can(user, "supervisor") && html`<button class="btn" onClick=${anchor} title="Write the chain tip to the off-box / write-once anchor directory">Anchor chain tip</button>`}
        ${can(user, "admin") && html`<a class="btn" href="/api/audit/export" download>Export JSONL</a>`}
        <button class="btn" onClick=${load}>Refresh</button></div>
      ${msg && html`<p class="sub">${msg}</p>`}
      ${err && html`<p class="err">${err}</p>`}
      ${v && html`<div class=${"banner " + (v.ok ? "ok" : "bad")} style=${{ margin: "12px 0" }}>
        ${v.ok ? `HASH CHAIN INTACT · ${v.entries} entries · head ${short(v.head, 16)} · ${v.anchors_checked} anchor(s) checked` : `CHAIN BROKEN at entry ${v.bad_seq}: ${v.reason}`}</div>`}
      ${data && !data.anchor_dir && html`<p class="sub">External anchoring is off - set SOVEREIGN_ANCHOR_DIR to a USB/WORM/off-box path so a rewritten chain is detectable.</p>`}
      <p class="sub">Each entry stores sha256(previous hash + entry). Editing or deleting any past line breaks every later hash.</p>
      <div class="scroll"><table><thead><tr><th>#</th><th>time</th><th>event</th><th>actor</th><th>what</th><th>hash</th></tr></thead><tbody>
        ${(data?.entries || []).map((e) => html`<tr key=${e.seq}><td class="num">${e.seq}</td><td class="sub">${e.ts}</td><td><code>${e.event}</code></td><td>${e.actor}</td><td>${summary(e)}</td><td class="num sub">${short(e.hash, 8)}</td></tr>`)}
      </tbody></table></div>
    </section>
    <section class="panel">
      <h2>Replay bundles · bit-exact forensic replay</h2>
      <p class="sub">Every solve stores the model hash, solver version, config (incl. deterministic flag) and full iteration log. Replay re-solves from the stored model and compares the SHA-256 of the solution vector.</p>
      <div class="scroll"><table><thead><tr><th>bundle</th><th>label</th><th>status</th><th class="r">objective</th><th class="r">iters</th><th>det.</th><th>model sha256</th><th></th></tr></thead><tbody>
        ${bundles.map((b) => {
          const r = replay[b.id];
          return html`<tr key=${b.id}><td class="num">${b.id}</td><td>${b.label}</td><td>${b.status}</td><td class="r num">${fmt(b.objective, 3)}</td><td class="r num">${b.iterations}</td><td>${b.deterministic ? "yes" : "no"}</td><td class="num sub">${short(b.model_sha256, 10)}</td>
            <td class="r">${r?.busy ? "replaying…" : r ? html`<span class=${"chip " + (r.ok ? "ok" : "bad")}>${r.reason}</span>` : html`<button class="btn small" onClick=${() => run(b.id)}>Replay</button>`}</td></tr>`;
        })}
      </tbody></table></div>
    </section>
  </div>`;
}
