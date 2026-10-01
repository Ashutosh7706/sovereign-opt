// "Digital twin" tab: the 3D refinery driven live by the solver (refinery_3d_digital_twin guide).
import { html, useState, useEffect, useRef, api, fmt, secs } from "./lib.js";
import { Refinery3D, webglAvailable } from "./twin3d.js";
import { Ticker, Burst } from "./fx.js";
import { TelemetryPanel } from "./telemetry.js";

export function Twin() {
  const box = useRef(null);
  const engine = useRef(null);
  const wsRef = useRef(null);
  const [layout, setLayout] = useState(null);
  const [stage, setStage] = useState("production");
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState("idle - press Solve & watch");
  const [iter, setIter] = useState(null);
  const [objective, setObjective] = useState(null);
  const [final, setFinal] = useState(null);
  const [sel, setSel] = useState(null);
  const [rotate, setRotate] = useState(false);
  const [err, setErr] = useState("");
  const [nodesState, setNodesState] = useState(null);
  const gl = webglAvailable();

  useEffect(() => {
    api(`/api/twin/layout?stage=${stage}`).then(setLayout).catch((e) => setErr(e.message));
  }, [stage]);

  useEffect(() => {
    if (!layout || !box.current || !gl) return;
    engine.current && engine.current.dispose();
    engine.current = new Refinery3D(box.current, layout, { onSelect: setSel });
    engine.current.autoRotate = rotate;
    const ro = new ResizeObserver(() => engine.current && engine.current.resize());
    ro.observe(box.current);
    setSel(null);
    run();  // first open or a different plan: solve it and show it live (never show another plan's numbers)
    return () => { ro.disconnect(); engine.current && engine.current.dispose(); engine.current = null; };
  }, [layout]);

  useEffect(() => { if (engine.current) engine.current.autoRotate = rotate; }, [rotate]);
  useEffect(() => () => wsRef.current && wsRef.current.close(), []);

  function apply(state) {
    if (engine.current && state) engine.current.setState(state);
    if (state) setNodesState(state.nodes);
  }

  function run() {
    if (wsRef.current) wsRef.current.close();
    setErr(""); setFinal(null); setRunning(true); setIter(null); setObjective(null); setNodesState(null);
    setPhase("connecting…");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/twin`);
    wsRef.current = ws;
    let done = false;
    ws.onopen = () => ws.send(JSON.stringify({ stage }));
    ws.onmessage = (ev) => {
      if (wsRef.current !== ws) return;  // a superseded solve (plan switched mid-run)
      const m = JSON.parse(ev.data);
      if (m.type === "layout") setPhase("solving: interior-point iterations on the LP relaxation");
      else if (m.type === "iter") { setIter(m); setPhase(`${m.phase} · iteration ${m.iter}`); if (m.objective != null) setObjective(m.objective); apply(m.state); }
      else if (m.type === "incumbent") { setPhase(m.phase); setObjective(m.objective); apply(m.state); }
      else if (m.type === "final") {
        done = true; setFinal(m); setRunning(false);
        if (m.objective != null) setObjective(m.objective);
        setPhase(m.status === "optimal" ? "optimal plan - colours show exact shadow prices" : `solver status: ${m.status}`);
        apply(m.state); ws.close();
      } else if (m.type === "error") { done = true; setErr(m.message); setRunning(false); }
    };
    ws.onclose = (e) => { if (wsRef.current !== ws) return; setRunning(false); if (!done) setErr(e.code === 4401 ? "Session expired - sign in again." : "Connection dropped - press Solve & watch to retry."); };
  }

  const th = layout ? layout.thresholds : { amber: 2, red: 8 };
  const bottlenecks = nodesState ? Object.entries(nodesState).filter(([, s]) => s.dual > 1e-6)
    .sort((a, b) => b[1].dual - a[1].dual).slice(0, 6) : [];
  const labelOf = (id) => (layout ? (layout.nodes.find((n) => n.id === id) || {}).label : id) || id;

  return html`<div class="grid">
    <section class="panel twin-panel">
      <div class="row">
        <h2 style=${{ margin: 0 }}>3D digital twin · live from the solver</h2>
        <span class="spacer"></span>
        <label class="sr-only" for="stg">plan</label>
        <select id="stg" value=${stage} onChange=${(e) => setStage(e.target.value)} disabled=${running}>
          <option value="production">Production plan</option><option value="shadow">Shadow plan (with trial constraints)</option>
        </select>
        <label class="chk"><input type="checkbox" checked=${rotate} onChange=${(e) => setRotate(e.target.checked)} /> auto-rotate</label>
        <button class="btn" onClick=${() => engine.current && engine.current.resetCamera()}>Reset view</button>
        <button class="btn primary" onClick=${run} disabled=${running || !gl}>${running ? "Solving…" : "Solve & watch"}</button>
      </div>
      <div class="twin-status">
        <div class="stat"><span class="l">Phase</span><span class="phase">${phase}</span></div>
        <div class="stat"><span class="l">Gross margin ($k/day)</span><${Ticker} value=${objective} digits=${1} className="big" /></div>
        <div class="stat"><span class="l">μ (complementarity)</span><span class="num">${final ? (final.status === "optimal" ? "converged" : "—") : iter ? iter.mu.toExponential(2) : "—"}</span></div>
        <div class="stat"><span class="l">Compute</span><span class="num small">${final ? `${final.device.toUpperCase()} · ${secs(final.seconds)}` : running ? "…" : "—"}</span></div>
      </div>
      ${err && html`<p class="err">${err}</p>`}
      ${!gl ? html`<div class="note bad">This browser has WebGL turned off, so the 3D view cannot render. The plan itself is on the Refinery plan tab.</div>` : html`
      <div class="twin-stage">
        <div ref=${box} class="twin-canvas" aria-label="3D refinery view: drag to rotate, scroll to zoom, click a unit for details"></div>
        <${Burst} fire=${final && final.status === "optimal" ? final.replay_id : null} label="OPTIMAL PLAN" />
        ${sel && html`<div class="twin-card" style=${{ borderColor: sel.color }} role="dialog" aria-label=${sel.label}>
          <button class="x" onClick=${() => setSel(null)} aria-label="close">×</button>
          <b>${sel.label}</b>
          <div class="kvs">
            <span>Level</span><span class="num">${(sel.target * 100).toFixed(1)}%${sel.capacity ? ` of ${fmt(sel.capacity, 0)} kbbl/d` : ""}</span>
            <span>${sel.kind === "product" ? "Sales" : sel.kind === "crude" ? "Crude run" : "Feed"}</span><span class="num">${fmt(sel.value, 1)} kbbl/d</span>
            ${sel.kind === "crude" && html`<span>Parcels bought</span><span class="num">${sel.parcels}</span>`}
            ${sel.kind === "unit" && html`<span>State</span><span>${sel.on ? "running" : "off"}</span>`}
            <span>In / out</span><span class="num">${fmt(sel.inflow, 1)} / ${fmt(sel.outflow, 1)} kbbl/d</span>
            <span>Shadow price</span><span class="num" style=${{ color: sel.color }}>$${fmt(sel.dual, 2)}/bbl${sel.bottleneck ? " (bottleneck)" : ""}</span>
          </div>
          ${sel.binding && html`<p class="sub">Binding: ${sel.binding}</p>`}
          ${sel.quality.length > 0 && html`<p class="sub">Quality-limited: ${sel.quality.join(", ")}</p>`}
          ${sel.dual > 1e-6 && html`<p class="sub">One more kbbl/d of this limit is worth ≈ $${fmt(sel.dual * 1000, 0)}/day of margin (y* from the solver).</p>`}
        </div>`}
        <div class="twin-legend">
          <span><i style=${{ background: "#00e5ff" }}></i>slack (y*=0)</span>
          <span><i style=${{ background: "#00ff9d" }}></i>active</span>
          <span><i style=${{ background: "#ffb300" }}></i>bottleneck > $${th.amber}/bbl</span>
          <span><i style=${{ background: "#ff1744" }}></i>severe ≥ $${th.red}/bbl</span>
          <span class="hint">drag = rotate · scroll = zoom · click = details</span>
        </div>
      </div>`}
    </section>
    <div class="grid g-side">
      <section class="panel">
        <h2>Where the plan is constrained</h2>
        ${bottlenecks.length === 0 ? html`<div class="empty">${running ? "Shadow prices appear as the solver converges…" : "No binding limit yet"}</div>` : html`
        <table><thead><tr><th>unit / tank</th><th class="r">shadow price</th><th>meaning</th></tr></thead><tbody>
          ${bottlenecks.map(([id, s]) => html`<tr key=${id} class="clickable" onClick=${() => { if (engine.current) { engine.current.selected = id; setSel(engine.current.describe(id)); } }}>
            <td>${labelOf(id)}</td>
            <td class="r num" style=${{ color: s.dual >= th.red ? "var(--bad)" : s.dual > th.amber ? "var(--warn)" : "var(--ok)" }}>$${fmt(s.dual, 2)}/bbl</td>
            <td class="sub">${s.binding || "active"}</td></tr>`)}
        </tbody></table>`}
        <p class="sub" style=${{ marginTop: 10 }}>During the solve, levels and colours come from the live interior-point iterate (the LP relaxation), then from each integer plan found, then from the final optimal plan with exact duals. ${final ? `Replay bundle ${final.replay_id}.` : ""}</p>
      </section>
      <${TelemetryPanel} compact=${true} />
    </div>
  </div>`;
}
