import { html, useState, useEffect, api, fmt, secs, short, can, t } from "./lib.js";

function Bar({ v, max, mk = [] }) {
  const w = max > 0 ? Math.min(100, (v / max) * 100) : 0;
  return html`<div class="bar"><div class="f" style=${{ width: w + "%" }}></div>${mk.map((m, i) => html`<div key=${i} class="mk" style=${{ left: Math.min(100, (m / max) * 100) + "%" }}></div>`)}</div>`;
}

export function Explanation({ exp }) {
  if (!exp) return null;
  return html`<div>
    <div class="banner bad">INFEASIBLE · ${exp.repairable ? "repair found" : "not repairable by soft constraints"}</div>
    <p>${exp.summary}</p>
    ${exp.repairs?.length > 0 && html`<h3>Minimum-change repair (all at once)</h3>
      <ul>${exp.repairs.map((r) => html`<li key=${r.row}>${r.text} ${r.safety && html`<span class="chip warn">safety sign-off</span>`}</li>`)}</ul>`}
    ${exp.alternatives?.length > 0 && html`<h3>Single-knob alternatives (pick one)</h3>
      <ul>${exp.alternatives.map((r) => html`<li key=${r.row}>${r.text} ${r.safety && html`<span class="chip warn">safety sign-off</span>`}</li>`)}</ul>`}
    ${exp.conflict?.length > 0 && html`<h3>Conflict set (elastic duals)</h3>
      <table><thead><tr><th>constraint</th><th>type</th><th class="r">dual weight</th></tr></thead><tbody>
      ${exp.conflict.map((c) => html`<tr key=${c.row}><td>${c.desc}</td><td>${c.hard ? html`<span class="chip">physics</span>` : html`<span class="chip accent">business</span>`}</td><td class="r num">${c.dual_weight.toPrecision(3)}</td></tr>`)}
      </tbody></table>`}
  </div>`;
}

function PlanColumn({ plan, base, title }) {
  if (!plan) return null;
  const rep = plan.report;
  const delta = base && plan.objective != null && base.objective != null ? plan.objective - base.objective : null;
  return html`<section class="panel">
    <h2>${title}</h2>
    <div class="row" style=${{ alignItems: "flex-end", gap: 24 }}>
      <div class="stat"><span class="l">Gross margin</span><span class="big">${plan.objective != null ? fmt(plan.objective, 1) : "—"}</span><span class="sub">$k / day</span></div>
      ${delta != null && html`<div class="stat"><span class="l">vs production</span><span class=${"big delta " + (delta >= 0 ? "pos" : "neg")}>${delta >= 0 ? "+" : ""}${fmt(delta, 1)}</span><span class="sub">$k / day</span></div>`}
      <div class="stat"><span class="l">Solve</span><span class="num">${plan.status} · ${secs(plan.seconds)}</span><span class="sub mono">replay ${plan.replay_id}</span></div>
    </div>
    ${plan.explanation && html`<div style=${{ marginTop: 12 }}><${Explanation} exp=${plan.explanation} /></div>`}
    ${plan.certificate && html`<p class="sub">Infeasibility proof: ${plan.certificate.type}${plan.certificate.verified ? " (verified)" : ""}${plan.certificate.detail ? " - " + plan.certificate.detail : ""}</p>`}
    ${plan.cond_warning && html`<p class="note warn">Numerically ill-conditioned solve (condition estimate ${Number(plan.cond_estimate).toExponential(1)}) - treat marginal values with care.</p>`}
    ${plan.message && html`<p class="note warn" style=${{ marginTop: 12 }}>${plan.message}</p>`}
    ${rep && html`
      <h3>Crude slate</h3>
      <div class="scroll"><table><thead><tr><th>crude</th><th class="r">S wt%</th><th class="r">parcels</th><th class="r">run kbbl/d</th></tr></thead><tbody>
        ${rep.crudes.map((c) => html`<tr key=${c.name}><td>${c.name} ${c.origin === "domestic" && html`<span class="chip ok">domestic</span>`}</td><td class="r num">${c.sulfur}</td><td class="r num">${c.parcels}</td><td class="r num">${fmt(c.run, 1)}</td></tr>`)}
      </tbody></table></div>
      <h3>Units</h3>
      <div class="scroll"><table><thead><tr><th>unit</th><th>state</th><th class="r">feed</th><th style=${{ width: "40%" }}>load (min | cap)</th></tr></thead><tbody>
        ${rep.units.map((u) => html`<tr key=${u.name}><td>${u.name}</td><td>${u.on ? html`<span class="chip ok">on</span>` : html`<span class="chip">off</span>`}</td><td class="r num">${fmt(u.feed, 1)}</td><td><${Bar} v=${u.feed} max=${u.capacity} mk=${[u.min_run]} /></td></tr>`)}
      </tbody></table></div>
      <h3>Product tanks</h3>
      <div class="scroll"><table><thead><tr><th>tank</th><th class="r">sales kbbl/d</th><th style=${{ width: "40%" }}>vs contract | market</th></tr></thead><tbody>
        ${rep.products.map((p) => html`<tr key=${p.name}><td>T${p.tank} ${p.name}</td><td class="r num">${fmt(p.sales, 1)}</td><td><${Bar} v=${p.sales} max=${p.max} mk=${p.min ? [p.min] : []} /></td></tr>`)}
      </tbody></table></div>
      <h3>Quality giveaway</h3>
      <div class="scroll"><table><thead><tr><th>spec</th><th class="r">limit</th><th class="r">achieved</th></tr></thead><tbody>
        ${rep.qualities.map((q) => html`<tr key=${q.product + q.prop + q.sense}><td>${q.product} ${q.prop === "ron" ? "RON" : "sulfur"} ${q.sense} ${q.safety && html`<span class="chip warn">safety</span>`}</td><td class="r num">${q.spec} ${q.unit}</td><td class="r num">${q.achieved == null ? "no blend" : q.achieved.toPrecision(4)}</td></tr>`)}
      </tbody></table></div>`}
    ${plan.shadow_prices?.length > 0 && html`<h3>What is binding (shadow prices)</h3>
      <table><tbody>${plan.shadow_prices.map((d) => html`<tr key=${d.row}><td>${d.label}</td><td class=${"r num delta " + (d.value >= 0 ? "pos" : "neg")}>${d.value >= 0 ? "+" : ""}${fmt(d.value, 2)}</td><td class="sub">${d.unit}</td></tr>`)}</tbody></table>`}
  </section>`;
}

export function Plan({ user }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [prices, setPrices] = useState({ HSD: 108, MS: 112, ATF: 110, VLSFO: 80 });
  const [crude, setCrude] = useState({ "Basrah Medium": 77.5, Murban: 85 });
  const [reopt, setReopt] = useState(null);

  async function run() {
    setBusy(true); setErr("");
    try { setData(await api("/api/plan/solve", { method: "POST" })); } catch (e) { setErr(e.message); }
    setBusy(false);
  }
  useEffect(() => { run(); }, []);

  async function act(c, what) {
    try { await api(`/api/nl/constraints/${c.id}/${what}`, { method: "POST", body: {} }); await run(); }
    catch (e) { setErr(e.message); }
  }
  const sup = can(user, "supervisor");
  async function reoptimize() {
    setErr("");
    try { setReopt(await api("/api/plan/reoptimize", { method: "POST", body: { product_prices: prices, crude_prices: crude } })); }
    catch (e) { setErr(e.message); }
  }

  const cons = data?.constraints || [];
  return html`<div class="grid">
    <section class="panel">
      <div class="row"><h2 style=${{ margin: 0 }}>Refinery plan · production vs shadow</h2><span class="spacer"></span>
        <button class="btn no-print" onClick=${() => window.print()} disabled=${!data}>${t("print")}</button>
        <button class="btn primary no-print" onClick=${run} disabled=${busy}>${busy ? "Solving…" : t("resolve")}</button></div>
      <p class="print-only">Printed ${new Date().toLocaleString()} by ${user.username} (${user.role}) - production replay ${data?.production?.replay_id || "-"}, model sha256 ${short(data?.production?.model_sha256, 16)}</p>
      <p class="sub">Advisory decision support only: the plan is never written to a control loop (Sec. 8, Phase A). Data is a synthetic MRPL-style model with illustrative numbers.</p>
      ${err && html`<p class="err">${err}</p>`}
      ${data?.needs_reverification?.length > 0 && html`<p class="note warn">${data.needs_reverification.length} active constraint(s) were produced by a parser/model version that is no longer configured - re-verify them: ${data.needs_reverification.map((c) => `“${c.text}” (${c.parser})`).join(", ")}</p>`}
      <h3>Active NL constraints</h3>
      ${cons.length === 0 ? html`<div class="empty">None yet. Use the Constraint Compiler tab.</div>` : html`
      <div class="scroll"><table><thead><tr><th>stage</th><th>constraint (as understood)</th><th>approved by</th><th></th></tr></thead><tbody>
        ${cons.map((c) => html`<tr key=${c.id}><td>${c.stage === "shadow" ? html`<span class="chip info">shadow</span>` : html`<span class="chip accent">production</span>`}</td>
          <td>${c.restatement}<div class="sub quote">“${c.text}”</div></td><td class="sub">${c.approved_by}<br/>${c.approved_at}</td>
          <td class="r no-print">${sup ? html`${c.stage === "shadow" && html`<button class="btn small good" onClick=${() => act(c, "promote")}>Promote</button>`} <button class="btn small danger" onClick=${() => act(c, "retire")}>Retire</button>` : html`<span class="sub">supervisor can promote / retire</span>`}</td></tr>`)}
      </tbody></table></div>`}
    </section>
    <div class=${"grid " + (data?.shadow ? "g2" : "")}>
      <${PlanColumn} plan=${data?.production} title="Production plan" />
      ${data?.shadow && html`<${PlanColumn} plan=${data.shadow} base=${data.production} title="Shadow plan (advisory, incl. shadow constraints)" />`}
    </div>
    <section class="panel no-print">
      <h2>Intra-day re-optimization · warm start</h2>
      <p class="sub">Today's parcels and unit on/off decisions stay fixed; prices move; flows are re-optimized. We solve the same LP from a cold LMS start and from the previous optimum and report both.</p>
      <div class="row">
        ${Object.keys(prices).map((k) => html`<label key=${k} class="sub">${k} $/bbl <input type="number" step="0.5" style=${{ width: 80 }} value=${prices[k]} onChange=${(e) => setPrices({ ...prices, [k]: +e.target.value })} /></label>`)}
        ${Object.keys(crude).map((k) => html`<label key=${k} class="sub">${k} $/bbl <input type="number" step="0.5" style=${{ width: 80 }} value=${crude[k]} onChange=${(e) => setCrude({ ...crude, [k]: +e.target.value })} /></label>`)}
        <button class="btn" onClick=${reoptimize}>Re-optimize</button>
      </div>
      ${reopt && html`<div class="grid g3" style=${{ marginTop: 12 }}>
        <div class="stat"><span class="l">Margin old → new ($k/d)</span><span class="num">${fmt(reopt.old_objective, 1)} → ${fmt(reopt.new_objective, 1)}</span></div>
        <div class="stat"><span class="l">Cold start (LMS)</span><span class="num">${reopt.cold.iters} iterations · ${secs(reopt.cold.seconds)}</span></div>
        <div class="stat"><span class="l">Warm start</span><span class="num">${reopt.warm.iters} iterations · ${secs(reopt.warm.seconds)} ${reopt.agree ? html`<span class="chip ok">same optimum</span>` : html`<span class="chip bad">differs</span>`}</span></div>
      </div>
      ${reopt.speedup_iters && html`<p><span class=${"chip " + (reopt.speedup_iters > 1 ? "ok" : "")}>${reopt.speedup_iters > 1 ? `${reopt.speedup_iters.toFixed(1)}× fewer iterations with warm start` : "warm start gave no iteration saving on this change"}</span> <span class="sub">measured on this change, not a general claim</span></p>`}`}
    </section>
  </div>`;
}
