import htm from "../vendor/htm.js";
import { I18N } from "./i18n.js";

export const React = window.React;
export const html = htm.bind(React.createElement);
export const { useState, useEffect, useRef, useMemo, useCallback } = React;

// ---------------------------------------------------------------- session + API
let csrf = null;
export const setCsrf = (tok) => { csrf = tok; };

export async function api(path, opts = {}) {
  const method = opts.method || "GET";
  const headers = { "Content-Type": "application/json" };
  if (method !== "GET" && csrf) headers["X-CSRF-Token"] = csrf;
  const r = await fetch(path, { ...opts, method, headers, credentials: "same-origin",
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  const txt = await r.text();
  let data;
  try { data = txt ? JSON.parse(txt) : null; } catch { data = { detail: txt }; }
  if (r.status === 401 && path !== "/api/auth/login") window.dispatchEvent(new Event("sov:logout"));
  if (!r.ok) {
    const e = new Error((data && (data.detail || data.message)) || r.statusText);
    e.status = r.status;
    throw e;
  }
  return data;
}

// ---------------------------------------------------------------- i18n scaffold (audit #91)
let lang = (() => { try { return localStorage.getItem("sov.lang") || "en"; } catch { return "en"; } })();
export const getLang = () => lang;
export function setLang(l) { lang = l; try { localStorage.setItem("sov.lang", l); } catch {} }
export const t = (key) => (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;

// ---------------------------------------------------------------- formatting
export const fmt = (v, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" :
  Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
export const fmtg = (v, p = 4) => (v === null || v === undefined ? "—" : Number(v).toPrecision(p));
export const fmtExp = (v) => (v === null || v === undefined ? "—" : Number(v).toExponential(2));
export const secs = (s) => (s === null || s === undefined ? "—" : s < 1 ? `${(s * 1000).toFixed(1)} ms` : `${s.toFixed(2)} s`);
export const short = (h, n = 10) => (h ? h.slice(0, n) : "—");
export const RANK = { operator: 1, supervisor: 2, admin: 3 };
export const can = (user, role) => !!user && (RANK[user.role] || 0) >= RANK[role];

/** Multi-series line chart; y may be log10-scaled. series: [{name, color, points:[[x,y]]}] */
export function LineChart({ series, log = false, height = 220, xLabel = "", vlines = [], yFmt, label = "chart" }) {
  const W = 640, H = height, L = 58, R = 12, T = 10, B = 26;
  const pts = series.flatMap((s) => s.points).filter((p) => p[1] !== null && Number.isFinite(p[1]) && (!log || p[1] > 0));
  if (!pts.length) return html`<div class="empty">No data yet</div>`;
  const tx = (v) => (log ? Math.log10(v) : v);
  let x0 = Math.min(...pts.map((p) => p[0])), x1 = Math.max(...pts.map((p) => p[0]));
  let y0 = Math.min(...pts.map((p) => tx(p[1]))), y1 = Math.max(...pts.map((p) => tx(p[1])));
  if (x1 === x0) x1 = x0 + 1;
  if (y1 === y0) { y1 += 1; y0 -= 1; }
  if (log) { y0 = Math.floor(y0); y1 = Math.ceil(y1); }
  else { const pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad; }
  const sx = (x) => L + ((x - x0) / (x1 - x0)) * (W - L - R);
  const sy = (y) => T + (1 - (tx(y) - y0) / (y1 - y0)) * (H - T - B);
  const yticks = [];
  if (log) {
    const step = Math.max(1, Math.ceil((y1 - y0) / 6));
    for (let e = y0; e <= y1; e += step) yticks.push({ v: 10 ** e, label: `1e${e}` });
  } else {
    for (let i = 0; i <= 4; i++) { const v = y0 + ((y1 - y0) * i) / 4; yticks.push({ v, label: yFmt ? yFmt(v) : fmtg(v, 4) }); }
  }
  const xticks = [];
  for (let i = 0; i < 5; i++) xticks.push(x0 + ((x1 - x0) * i) / 5);
  return html`
    <div>
      <svg class="chart" viewBox=${`0 0 ${W} ${H}`} role="img" aria-label=${label}>
        <g class="grid">
          ${yticks.map((tk) => html`<line key=${"y" + tk.label} x1=${L} x2=${W - R} y1=${sy(tk.v)} y2=${sy(tk.v)} />`)}
        </g>
        ${yticks.map((tk) => html`<text key=${"yl" + tk.label} x=${L - 6} y=${sy(tk.v) + 3} text-anchor="end">${tk.label}</text>`)}
        ${xticks.map((x, i) => html`<text key=${"x" + i} x=${sx(x)} y=${H - 8} text-anchor="middle">${Math.round(x)}</text>`)}
        ${xLabel && html`<text x=${W - R} y=${H - 8} text-anchor="end">${xLabel}</text>`}
        ${vlines.map((v, i) => html`<g key=${"v" + i}><line x1=${sx(v.x)} x2=${sx(v.x)} y1=${T} y2=${H - B} stroke="var(--muted)" stroke-dasharray="3 3" /><text x=${sx(v.x) + 4} y=${T + 10} text-anchor="start">${v.label}</text></g>`)}
        ${series.map((s) => {
          const p = s.points.filter((q) => q[1] !== null && Number.isFinite(q[1]) && (!log || q[1] > 0));
          if (!p.length) return null;
          const d = p.map((q, i) => `${i ? "L" : "M"}${sx(q[0]).toFixed(1)},${sy(q[1]).toFixed(1)}`).join(" ");
          return html`<path key=${s.name} d=${d} fill="none" stroke=${s.color} stroke-width="2" stroke-linejoin="round" stroke-dasharray=${s.dash || ""} />`;
        })}
      </svg>
      <div class="legend">${series.map((s) => html`<span key=${s.name}><i style=${{ background: s.color }}></i>${s.name}</span>`)}</div>
    </div>`;
}
