// Algorithm visualisations (dashboard upgrade plan, features 7 and 8) - both driven by real solver events.
import { html, useMemo } from "./lib.js";

/** Central path: every dot is one (x_j, s_j) complementarity pair of the live IPM iterate, in log10.
 *  On the central path x_j * s_j = mu, i.e. a line of slope -1; as mu -> 0 the line slides toward
 *  the corner and the pairs split into "x_j -> 0" and "s_j -> 0" groups (strict complementarity). */
export function CentralPath({ iters }) {
  const withPairs = iters.filter((e) => e.xs_pairs && e.xs_pairs.length);
  if (!withPairs.length) return html`<div class="empty">Streams during an LP solve (root LP for MIPs)</div>`;
  const cur = withPairs[withPairs.length - 1];
  const W = 320, H = 320, L = 34, B = 26, lo = -12, hi = 3;
  const sx = (v) => L + ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * (W - L - 8);
  const sy = (v) => H - B - ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * (H - B - 8);
  const lmu = Math.log10(Math.max(cur.mu, 1e-300));
  // line log x + log s = log mu
  const x1 = lo, y1 = lmu - lo, x2 = hi, y2 = lmu - hi;
  const trail = withPairs.slice(-8, -1);
  const ticks = [-12, -9, -6, -3, 0, 3];
  return html`<div>
    <svg class="chart" viewBox=${`0 0 ${W} ${H}`} role="img" aria-label="central path complementarity pairs">
      ${ticks.map((t) => html`<g key=${t} class="grid"><line x1=${sx(t)} x2=${sx(t)} y1=${8} y2=${H - B} /><line x1=${L} x2=${W - 8} y1=${sy(t)} y2=${sy(t)} /></g>`)}
      ${ticks.map((t) => html`<text key=${"x" + t} x=${sx(t)} y=${H - 10} text-anchor="middle">${t}</text>`)}
      ${ticks.map((t) => html`<text key=${"y" + t} x=${L - 4} y=${sy(t) + 3} text-anchor="end">${t}</text>`)}
      <text x=${W - 8} y=${H - 10} text-anchor="end">log x</text>
      <text x=${L + 2} y=${16}>log s</text>
      <line x1=${sx(x1)} y1=${sy(y1)} x2=${sx(x2)} y2=${sy(y2)} stroke="var(--s4)" stroke-dasharray="4 3" class="mu-line" />
      ${trail.map((e, k) => e.xs_pairs.map((p, i) => html`<circle key=${k + "-" + i} cx=${sx(p[0])} cy=${sy(p[1])} r="1.4" fill="var(--s2)" opacity=${0.08 + 0.05 * k} />`))}
      ${cur.xs_pairs.map((p, i) => html`<circle key=${"c" + i} class="cp-dot" cx=${sx(p[0])} cy=${sy(p[1])} r="2.6" fill="var(--s1)" />`)}
    </svg>
    <div class="legend"><span><i style=${{ background: "var(--s1)" }}></i>(x_j, s_j) now</span>
      <span><i style=${{ background: "var(--s4)" }}></i>x·s = μ (${cur.mu.toExponential(1)})</span>
      <span>iteration ${cur.iter}</span></div>
  </div>`;
}

const STATE_COLOR = { open: "var(--warn)", branched: "var(--s2)", pruned: "var(--faint)", infeasible: "var(--bad)", integral: "var(--ok)" };

/** Branch-and-bound tree, laid out by depth; nodes change colour live as they are explored. */
export function BnBTree({ events }) {
  const tree = useMemo(() => {
    const nodes = new Map();
    for (const e of events) {
      const n = nodes.get(e.id) || { id: e.id, parent: null, label: "", state: "open", bound: null, children: [] };
      if (e.parent != null && n.parent == null) {
        n.parent = e.parent;
        const p = nodes.get(e.parent);
        if (p && !p.children.includes(e.id)) p.children.push(e.id);
      }
      if (e.label) n.label = e.label;
      n.state = e.state || n.state;
      if (e.bound != null) n.bound = e.bound;
      nodes.set(e.id, n);
    }
    // tidy layout: leaves get consecutive x, parents centred over children
    let leaf = 0;
    const pos = new Map();
    const place = (id, depth) => {
      const n = nodes.get(id);
      if (!n) return 0;
      const kids = n.children.filter((c) => nodes.has(c));
      let x;
      if (!kids.length) x = leaf++;
      else { const xs = kids.map((c) => place(c, depth + 1)); x = (Math.min(...xs) + Math.max(...xs)) / 2; }
      pos.set(id, { x, depth });
      return x;
    };
    if (nodes.has(0)) place(0, 0);
    return { nodes, pos, leaves: Math.max(1, leaf) };
  }, [events]);
  if (!tree.nodes.size) return html`<div class="empty">Streams for mixed-integer models</div>`;
  const maxDepth = Math.max(0, ...[...tree.pos.values()].map((p) => p.depth));
  const W = 640, H = Math.max(160, 50 + maxDepth * 46);
  const X = (x) => 16 + (tree.leaves === 1 ? (W - 32) / 2 : (x / (tree.leaves - 1)) * (W - 32));
  const Y = (d) => 20 + d * 46;
  const counts = {};
  tree.nodes.forEach((n) => { counts[n.state] = (counts[n.state] || 0) + 1; });
  return html`<div>
    <svg class="chart bnb" viewBox=${`0 0 ${W} ${H}`} role="img" aria-label="branch and bound tree">
      ${[...tree.nodes.values()].filter((n) => n.parent != null && tree.pos.has(n.id) && tree.pos.has(n.parent)).map((n) => {
        const a = tree.pos.get(n.parent), b = tree.pos.get(n.id);
        return html`<line key=${"e" + n.id} x1=${X(a.x)} y1=${Y(a.depth)} x2=${X(b.x)} y2=${Y(b.depth)} class="bnb-edge" />`;
      })}
      ${[...tree.nodes.values()].filter((n) => tree.pos.has(n.id)).map((n) => {
        const p = tree.pos.get(n.id);
        return html`<g key=${"n" + n.id} class=${"bnb-node s-" + n.state}>
          <circle cx=${X(p.x)} cy=${Y(p.depth)} r=${n.id === 0 ? 8 : 6} fill=${STATE_COLOR[n.state] || "var(--muted)"} />
          <title>${(n.label || "root") + " · " + n.state + (n.bound != null ? " · bound " + n.bound.toFixed(2) : "")}</title>
        </g>`;
      })}
    </svg>
    <div class="legend">${Object.entries(STATE_COLOR).map(([k, c]) => html`<span key=${k}><i style=${{ background: c }}></i>${k} ${counts[k] || 0}</span>`)}</div>
  </div>`;
}
