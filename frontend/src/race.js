import { html, useState, useEffect, useRef, api, fmt, fmtExp, secs, short, LineChart } from "./lib.js";
import { Ticker, TerminalLog, Burst, Tilt } from "./fx.js";
import { CentralPath, BnBTree } from "./viz.js";
import { TelemetryPanel } from "./telemetry.js";

const LANES = [
  { lane: "sovereign", name: "Sovereign IPM", sub: "this platform",
    req: "Always available. Our interior-point solver (+ branch-and-bound for integer decisions), on GPU if one is present, else CPU." },
  { lane: "highs", name: "HiGHS (default)", sub: "open-source reference",
    req: "Free open-source solver bundled with SciPy, default algorithm (dual simplex for LPs). Used as the independent 'right answer' check." },
  { lane: "highs_ipm", name: "HiGHS (IPM)", sub: "same algorithm family as ours",
    req: "HiGHS forced onto its own interior-point method - the like-for-like comparison with our IPM. Not applicable to integer models." },
  { lane: "gurobi_cpu", name: "Gurobi CPU", sub: "incumbent, multithreaded",
    req: "The commercial solver most refineries already license. Needs gurobipy + a licence on this machine." },
  { lane: "gurobi_gpu", name: "Gurobi GPU", sub: "GPU / PDHG mode",
    req: "Gurobi's GPU mode. Needs gurobipy, a licence with GPU support, and an NVIDIA GPU with CUDA." },
];

export function Race() {
  const [models, setModels] = useState([]);
  const [modelKey, setModelKey] = useState("refinery");
  const [precision, setPrecision] = useState("mixed");
  const [deterministic, setDeterministic] = useState(true);
  const [deviceMode, setDeviceMode] = useState("auto");
  const [trees, setTrees] = useState([]);
  const [logLines, setLogLines] = useState([]);
  const [live, setLive] = useState(null);
  const [running, setRunning] = useState(false);
  const [info, setInfo] = useState(null);
  const [lanes, setLanes] = useState({});
  const [iters, setIters] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [verdict, setVerdict] = useState(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const [upload, setUpload] = useState("");
  const wsRef = useRef(null);

  const loadModels = () => api("/api/models").then(setModels).catch((e) => setError(e.message));
  useEffect(() => { loadModels(); }, []);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(t);
  }, [running]);
  useEffect(() => () => wsRef.current && wsRef.current.close(), []);

  function start() {
    setError(""); setInfo(null); setLanes({}); setIters([]); setNodes([]); setVerdict(null); setRunning(true);
    setTrees([]); setLive(null); setLogLines(["$ sovereign solve --model " + modelKey + " --precision " + precision + " --device " + deviceMode]);
    const say = (line) => setLogLines((a) => [...a.slice(-400), line]);
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/race`);
    wsRef.current = ws;
    ws.onopen = () => ws.send(JSON.stringify({ model: modelKey, precision, deterministic, device: deviceMode }));
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "model") { setInfo(m); say(`model ${m.name}: ${m.m} rows x ${m.n} cols, ${m.nnz} nnz${m.integers ? `, ${m.integers} integer` : ""}`); }
      else if (m.type === "lane_start") { setLanes((l) => ({ ...l, [m.lane]: { ...m, state: "running", t0: Date.now() } })); say(`[${m.name}] started`); }
      else if (m.type === "lane_done") {
        setLanes((l) => ({ ...l, [m.lane]: { ...l[m.lane], ...m, state: m.status === "unavailable" ? "unavailable" : "done" } }));
        say(m.seconds == null ? `[${m.name || m.lane}] ${m.status}: ${m.note || ""}` :
          `[${m.name || m.lane}] ${m.status} in ${secs(m.seconds)}  objective ${fmt(m.objective, 6)}`);
        if (m.lane === "sovereign" && m.gpu_decision) say(`device decision: ${m.gpu_decision}`);
      }
      else if (m.type === "iter") {
        setIters((a) => [...a, m]);
        if (m.obj_model != null) setLive(m.obj_model);
        say(`it ${String(m.iter).padStart(3)}  obj ${m.obj_model != null ? fmt(m.obj_model, 3).padStart(12) : fmtExp(m.pobj)}  pinf ${fmtExp(m.pinf)}  dinf ${fmtExp(m.dinf)}  gap ${fmtExp(m.gap)}  mu ${fmtExp(m.mu)}  ${m.precision}`);
      }
      else if (m.type === "tree") setTrees((a) => [...a, m]);
      else if (m.type === "node") {
        setNodes((a) => [...a, m]);
        if (m.kind === "incumbent" && m.incumbent != null) { setLive(m.incumbent); say(`B&B: new incumbent ${fmt(m.incumbent, 4)} (from ${m.source || "search"})  best bound ${fmt(m.bound, 4)}  gap ${m.gap != null ? (100 * m.gap).toFixed(2) + "%" : "—"}`); }
      }
      else if (m.type === "verdict") setVerdict(m);
      else if (m.type === "error") { setError(m.message); setRunning(false); }
      else if (m.type === "done") { setRunning(false); ws.close(); }
    };
    let finished = false;
    const onmsg = ws.onmessage;
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.type === "done" || m.type === "error") finished = true; onmsg(ev); };
    ws.onerror = () => {};
    ws.onclose = (ev) => {
      setRunning(false);
      if (!finished) setError(ev.code === 4401 ? "Session expired - please sign in again." :
        "Connection to the server dropped mid-race. Results received so far are kept; press Start race to retry.");
    };
  }

  async function onFile(e) {
    const f = e.target.files[0];
    if (!f) return;
    setUpload("reading…");
    try {
      const text = await f.text();
      const r = await api("/api/models/mps", { method: "POST", body: { name: f.name, text } });
      setUpload(`loaded ${r.name}: ${r.m} rows × ${r.n} cols, ${r.nnz} nnz${r.integers ? `, ${r.integers} integer` : ""}${r.maximize ? ", OBJSENSE MAX" : ""}`);
      await loadModels();
      setModelKey(r.key);
    } catch (err) { setUpload("error: " + err.message); }
    e.target.value = "";
  }

  async function onReadme(e) {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const r = await api("/api/netlib/readme", { method: "POST", body: { text: await f.text() } });
      setUpload(`Netlib readme: ${r.parsed} reference optima loaded (used by the verdict when a model name matches)`);
      await loadModels();
    } catch (err) { setUpload("error: " + err.message); }
    e.target.value = "";
  }

  const done = Object.values(lanes).filter((l) => l.state === "done" && l.seconds != null);
  const maxT = Math.max(0.001, ...done.map((l) => l.seconds), ...Object.values(lanes).filter((l) => l.state === "running").map((l) => (now - l.t0) / 1000));
  const switchIter = lanes.sovereign && lanes.sovereign.fp64_switch_iter;
  const liveSwitch = iters.find((e) => e.precision === "fp64");
  const vline = (switchIter ?? (liveSwitch && liveSwitch.iter));
  const bnbPts = nodes.filter((n) => n.incumbent !== null || n.bound !== null);
  const incSources = nodes.filter((n) => n.kind === "incumbent").map((n) => n.source || "search");
  const ranked = done.filter((l) => ["optimal", "feasible"].includes(l.status)).sort((a, b) => a.seconds - b.seconds).map((l) => l.lane);

  return html`
  <div class="grid">
    <section class="panel">
      <h2>Benchmark race · fair protocol</h2>
      <div class="row">
        <select value=${modelKey} onChange=${(e) => setModelKey(e.target.value)} aria-label="model">
          ${models.map((m) => html`<option key=${m.key} value=${m.key}>${m.name}</option>`)}
        </select>
        <select value=${precision} onChange=${(e) => setPrecision(e.target.value)} aria-label="precision">
          <option value="mixed">Mixed precision (FP32 factor → FP64 refine)</option>
          <option value="fp64">FP64 throughout</option>
        </select>
        <select value=${deviceMode} onChange=${(e) => setDeviceMode(e.target.value)} aria-label="compute device"
          title="Auto sends a problem to the GPU only when it is big enough for the GPU to win">
          <option value="auto">Device: Auto (size-based)</option>
          <option value="gpu">Device: Force GPU</option>
          <option value="cpu">Device: CPU only</option>
        </select>
        <label class="chk"><input type="checkbox" checked=${deterministic} onChange=${(e) => setDeterministic(e.target.checked)} /> deterministic mode</label>
        <span class="spacer"></span>
        <label class="btn" style=${{ cursor: "pointer" }} title="Netlib lp/data/readme - reference optima are read from it, never hand-typed">Load Netlib readme…<input type="file" onChange=${onReadme} style=${{ display: "none" }} /></label>
        <label class="btn" style=${{ cursor: "pointer" }}>Load MPS…<input type="file" accept=".mps,.MPS,.txt,.QPS" onChange=${onFile} style=${{ display: "none" }} /></label>
        <button class="btn primary" onClick=${start} disabled=${running}>${running ? "Racing…" : "Start race"}</button>
      </div>
      ${upload && html`<p class="sub">${upload}</p>`}
      <p class="sub">Lanes run one after another on the same machine so no solver steals cores from another. Every lane is always shown; a solver that is not installed stays on screen as <em>unavailable</em>.</p>
      ${error && html`<p class="err">${error}</p>`}
      ${(running || live != null) && html`<div class="hero-live">
        <div><span class="l">${running ? "live objective (display sense)" : "final objective"}</span>
          <${Ticker} value=${lanes.sovereign && lanes.sovereign.objective != null ? lanes.sovereign.objective : live} digits=${4} className="hero" /></div>
        <div><span class="l">IPM iteration</span><span class="num hero-s">${iters.length ? iters[iters.length - 1].iter : "—"}</span></div>
        <div><span class="l" style=${{ textTransform: "none" }}>μ (COMPLEMENTARITY)</span><span class="num hero-s">${iters.length ? fmtExp(iters[iters.length - 1].mu) : "—"}</span></div>
        <div><span class="l">B&B nodes</span><span class="num hero-s">${trees.length ? new Set(trees.map((e) => e.id)).size : "—"}</span></div>
        <${Burst} fire=${lanes.sovereign && lanes.sovereign.status === "optimal" ? lanes.sovereign.replay_id : null} label="OPTIMAL" />
      </div>`}
      ${gpuBanner(info, lanes.sovereign, deviceMode)}
      ${info && html`<p class="sub mono">${info.name} · ${info.m.toLocaleString()} rows × ${info.n.toLocaleString()} cols · ${info.nnz.toLocaleString()} nnz${info.integers ? ` · ${info.integers} integer vars` : ""} · sha256 ${short(info.sha256, 12)}${info.maximize ? " · maximize" : ""}</p>`}
      <div style=${{ marginTop: 8 }}>
        ${LANES.map(({ lane, name, sub }) => {
          const l = lanes[lane];
          const t = !l ? null : l.state === "running" ? (now - l.t0) / 1000 : l.seconds;
          const w = t == null ? 0 : Math.max(0.5, (t / maxT) * 100);
          const rank = l && l.state === "done" && ["optimal", "feasible"].includes(l.status) ? ranked.indexOf(lane) + 1 : 0;
          const cls = (!l ? "" : l.state === "running" ? "run" : lane === "sovereign" ? "us" : "") + (rank === 1 && ranked.length > 1 ? " lead" : "");
          return html`<div class="lane" key=${lane}>
            <div class="nm" title=${LANES.find((x) => x.lane === lane).req}>${l && l.name ? l.name : name}<small>${sub}</small></div>
            <div class="track"><div class=${"fill " + cls} style=${{ width: (l && l.state === "unavailable" ? 0 : w) + "%" }}></div>
              <span class="t">${!l ? "waiting" : l.state === "unavailable" ? "unavailable - " + (l.note || "") : l.seconds == null && l.state === "done" ? `${l.status}${l.note ? " - " + l.note : ""}` : l.state === "running" ? "running " + secs(t) : `${secs(t)} · ${l.status}`}</span></div>
            <div class="obj">${rank ? html`<span class=${"rank r" + rank}>#${rank}</span>` : ""}${l && l.objective != null ? fmt(l.objective, 4) : "—"}</div>
          </div>`;
        })}
      </div>
      <details class="legend-box">
        <summary>What do the lanes mean? (for non-technical viewers)</summary>
        <dl class="kv">${LANES.map((x) => html`<dt key=${x.lane + "t"}>${x.name}</dt><dd key=${x.lane + "d"} class="plain">${x.req}</dd>`)}</dl>
        <p class="sub">A shorter bar is faster. "Unavailable" is not a failure of our product - the protocol (roadmap Sec. 5) insists every lane is shown, so a missing competitor is displayed rather than hidden. The objective column must match across lanes: that is the correctness check.</p>
      </details>
    </section>

    <div class="grid g2">
      <section class="panel">
        <h2>IPM convergence (root LP) · log scale</h2>
        <${LineChart} log=${true} xLabel="iteration" vlines=${vline != null ? [{ x: vline, label: "FP32→FP64" }] : []}
          series=${[
            { name: "primal infeas.", color: "var(--s1)", points: iters.map((e) => [e.iter, e.pinf]) },
            { name: "dual infeas.", color: "var(--s2)", points: iters.map((e) => [e.iter, e.dinf]) },
            { name: "rel. gap", color: "var(--s3)", points: iters.map((e) => [e.iter, e.gap]) },
            { name: "μ", color: "var(--s4)", points: iters.map((e) => [e.iter, e.mu]), dash: "4 3" },
          ]} />
      </section>
      <section class="panel">
        <h2>Branch & bound · incumbent vs bound</h2>
        ${bnbPts.length ? html`<${LineChart} xLabel="nodes" yFmt=${(v) => fmt(v, 1)}
          series=${[
            { name: "best bound", color: "var(--s2)", points: bnbPts.map((n) => [n.node < 0 ? 0 : n.node, n.bound]) },
            { name: "incumbent", color: "var(--s1)", points: bnbPts.filter((n) => n.incumbent != null).map((n) => [n.node < 0 ? 0 : n.node, n.incumbent]) },
          ]} />` : html`<div class="empty">${info && !info.integers ? "Pure LP - no branching needed" : "Runs for mixed-integer models"}</div>`}
      </section>
    </div>

    <div class="grid g2">
      <section class="panel">
        <h2>Central path · live complementarity pairs</h2>
        <p class="sub">Each dot is one variable's (x<sub>j</sub>, s<sub>j</sub>) from the current iterate. The IPM keeps them near the line x·s = μ and slides it to the corner - that is the “interior” path to the optimum.</p>
        <${CentralPath} iters=${iters} />
      </section>
      <section class="panel">
        <h2>Branch & bound · search tree</h2>
        <p class="sub">Every node the solver explores, live: amber = open, blue = branched, grey = pruned by bound, red = infeasible, green = integer-feasible.</p>
        <${BnBTree} events=${trees} />
        ${incSources.length > 0 && html`<p class="sub">Incumbents found: ${incSources.length} · latest from ${incSources[incSources.length - 1]}. A rounding-heuristic incumbent comes from a relaxed node, so it has no green leaf of its own.</p>`}
      </section>
    </div>

    <div class="grid g-side">
      <section class="panel">
        <h2>Solver log · streaming</h2>
        <${TerminalLog} lines=${logLines} title="sovereign-ipm" height=${230} />
      </section>
      <${TelemetryPanel} />
    </div>

    ${(verdict || lanes.sovereign?.state === "done") && html`
    <div class="grid g-side">
      <section class="panel">
        <h2>Verdict</h2>
        ${verdict && html`
          <div class="grid g3" style=${{ marginBottom: 12 }}>
            <${Tilt}><div class="stat"><span class="l">Our objective</span><span class="num">${fmt(verdict.objective, 6)}</span></div><//>
            <${Tilt}><div class="stat"><span class="l">Reference (${verdict.reference_source || "none"})</span><span class="num">${fmt(verdict.reference, 6)}</span></div><//>
            <${Tilt}><div class="stat"><span class="l">Agreement</span><span>${verdict.agrees == null ? html`<span class="chip">no reference</span>` : verdict.agrees ? html`<span class="chip ok">matches (≤1e-6 rel)</span>` : html`<span class="chip bad">MISMATCH</span>`}</span></div><//>
          </div>
          ${verdict.beaten_by.length > 0 ? html`<div class="note accent"><b>Faster lanes on this model:</b> ${verdict.beaten_by.join(", ")}. Honest pitch line:<br/><em>“${verdict.pitch_line}”</em></div>`
            : html`<div class="note ok">${verdict.pitch_line}</div>`}
          ${verdict.unavailable.length > 0 && html`<p class="note warn" style=${{ marginTop: 10 }}>Protocol incomplete: ${verdict.unavailable.join(", ")} not run on this machine. Do not quote a speed claim until those lanes have been run (Sec. 5).</p>`}`}
      </section>
      ${lanes.sovereign?.state === "done" && html`
      <section class="panel">
        <h2>Our run · forensic details</h2>
        <dl class="kv">
          <dt>status</dt><dd>${lanes.sovereign.status}</dd>
          <dt>wall time</dt><dd>${secs(lanes.sovereign.seconds)}</dd>
          <dt>IPM iterations</dt><dd>${lanes.sovereign.iters}${lanes.sovereign.nodes ? ` over ${lanes.sovereign.lp_solves} LP solves, ${lanes.sovereign.nodes} B&B nodes` : ""}</dd>
          <dt>precision</dt><dd>${lanes.sovereign.precision}${lanes.sovereign.fp64_switch_iter != null ? ` · FP64 from iter ${lanes.sovereign.fp64_switch_iter}` : ""}</dd>
          <dt>device / solver</dt><dd>${lanes.sovereign.device} · ${lanes.sovereign.linear_solver}</dd>
          <dt>algorithm</dt><dd>${lanes.sovereign.algorithm || "mehrotra"}</dd>
          <dt>condition est.</dt><dd>${fmtExp(lanes.sovereign.cond_estimate)}${lanes.sovereign.cond_warning ? " ⚠ ill-conditioned" : ""}</dd>
          <dt>max violation</dt><dd>${fmtExp(lanes.sovereign.max_violation)}</dd>
          <dt>deterministic</dt><dd>${String(lanes.sovereign.deterministic)}</dd>
          <dt>solution sha256</dt><dd>${short(lanes.sovereign.x_sha256, 24)}…</dd>
          <dt>replay bundle</dt><dd>${lanes.sovereign.replay_id}</dd>
        </dl>
      </section>`}
    </div>`}
  </div>`;
}

const SMALL_MSG = "Problem too small for GPU offload — running CPU path (this is the correct engineering choice, not a limitation).";

/** Dashboard plan Sec. 1: say out loud which device ran and why. */
function gpuBanner(info, lane, mode) {
  const d = lane && lane.gpu_decision;
  if (!info) return null;
  if (!d) {
    if (mode === "auto" && info.m < info.gpu_min_rows)
      return html`<div class="note gpu-note cpu">${SMALL_MSG} <span class="sub">(${info.m.toLocaleString()} rows &lt; ${info.gpu_min_rows.toLocaleString()}-row GPU threshold)</span></div>`;
    return null;
  }
  const onGpu = d.startsWith("GPU");
  const small = d.includes("too small");
  const fallback = d.includes("fallback");
  return html`<div class=${"note gpu-note " + (onGpu ? "gpu" : fallback ? "warn" : "cpu")} role="status">
    <b>${onGpu ? "GPU path" : small ? SMALL_MSG : fallback ? "GPU fell back to CPU" : "CPU path"}</b>
    ${!small && html`<span> — ${d}</span>`}
    ${small && html`<span class="sub"> Pick a “Dense LP … (GPU showcase)” model to see the GPU lane win.</span>`}
  </div>`;
}
