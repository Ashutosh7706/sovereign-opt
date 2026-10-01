// Engagement components (dashboard upgrade plan, features 2, 5, 6, 9): every one of them renders
// real data from the solver or the hardware - the animation only changes how it arrives on screen.
import { html, useState, useEffect, useRef, fmt } from "./lib.js";

const reduced = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Animated number that eases toward each new real value (stock-ticker style). */
export function Ticker({ value, digits = 2, prefix = "", suffix = "", className = "" }) {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  const target = useRef(value);
  const [dir, setDir] = useState("");
  const raf = useRef(0);
  useEffect(() => {
    if (value != null && target.current != null && value !== target.current) setDir(value > target.current ? " up" : " down");
    target.current = value;
    if (value == null || !Number.isFinite(value)) { setShown(value); return; }
    if (reduced() || document.hidden || shown == null || !Number.isFinite(shown)) { setShown(value); from.current = value; return; }
    const start = performance.now(), a = from.current ?? value, dur = 450;
    cancelAnimationFrame(raf.current);
    const step = (t) => {
      const k = Math.min(1, (t - start) / dur), e = 1 - Math.pow(1 - k, 3);
      const v = a + (value - a) * e;
      setShown(v);
      from.current = v;
      if (k < 1) raf.current = requestAnimationFrame(step);
    };
    raf.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf.current);
  }, [value]);
  return html`<span class=${"ticker num " + className + dir}>${prefix}${fmt(shown, digits)}${suffix}</span>`;
}

/** Terminal-style live solver log with a typewriter reveal of the newest line. */
export function TerminalLog({ lines, title = "solver log", height = 190 }) {
  const box = useRef(null);
  const [typed, setTyped] = useState("");
  const last = lines.length ? lines[lines.length - 1] : "";
  useEffect(() => {
    if (reduced()) { setTyped(last); return; }
    let i = 0;
    const id = setInterval(() => { i += 6; setTyped(last.slice(0, i)); if (i >= last.length) clearInterval(id); }, 12);
    return () => clearInterval(id);
  }, [last]);
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [lines.length, typed]);
  return html`<div class="term" role="log" aria-live="off" aria-label=${title}>
    <div class="term-bar"><i></i><i></i><i></i><span>${title}</span></div>
    <pre ref=${box} style=${{ height }}>${lines.slice(0, -1).slice(-300).join("\n")}${lines.length > 1 ? "\n" : ""}${typed}<b class="caret">▌</b></pre>
  </div>`;
}

/** Radial gauge (SVG arc) for a real measurement. */
export function Gauge({ label, value, max = 100, unit = "%", color = "var(--accent)", sub = "" }) {
  const v = value == null || !Number.isFinite(value) ? null : Math.max(0, Math.min(max, value));
  const R = 38, C = 2 * Math.PI * R, arc = 0.75;  // 270° dial
  const frac = v == null ? 0 : v / max;
  return html`<div class="gauge" role="meter" aria-label=${label} aria-valuenow=${v ?? 0} aria-valuemin="0" aria-valuemax=${max}>
    <svg viewBox="0 0 100 100">
      <circle cx="50" cy="50" r=${R} class="g-track" stroke-dasharray=${`${C * arc} ${C}`} transform="rotate(135 50 50)" />
      <circle cx="50" cy="50" r=${R} class="g-val" stroke=${color}
        stroke-dasharray=${`${C * arc * frac} ${C}`} transform="rotate(135 50 50)" />
      <text x="50" y="50" class="g-num">${v == null ? "—" : (max >= 1000 ? Math.round(v) : v.toFixed(0))}</text>
      <text x="50" y="64" class="g-unit">${unit}</text>
    </svg>
    <div class="g-label">${label}</div>
    ${sub && html`<div class="g-sub">${sub}</div>`}
  </div>`;
}

export function Sparkline({ points, max, color = "var(--accent)", height = 34 }) {
  const W = 160, H = height;
  const pts = points.filter((p) => p != null && Number.isFinite(p));
  if (pts.length < 2) return html`<svg class="spark" viewBox=${`0 0 ${W} ${H}`}></svg>`;
  const mx = max ?? Math.max(...pts, 1);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${((i / (pts.length - 1)) * W).toFixed(1)},${(H - 2 - (p / mx) * (H - 4)).toFixed(1)}`).join(" ");
  return html`<svg class="spark" viewBox=${`0 0 ${W} ${H}`} preserveAspectRatio="none"><path d=${d} fill="none" stroke=${color} stroke-width="1.6" /></svg>`;
}

/** Brief particle burst + check mark when a solve reaches optimal (feature 9, cosmetic). */
export function Burst({ fire, label = "OPTIMAL" }) {
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (!fire) return;
    setOn(true);
    const id = setTimeout(() => setOn(false), 1700);
    return () => clearTimeout(id);
  }, [fire]);
  if (!on || reduced()) return null;
  const parts = Array.from({ length: 18 }, (_, i) => i);
  return html`<div class="burst" aria-hidden="true">
    ${parts.map((i) => html`<i key=${i} style=${{ "--a": `${(360 / parts.length) * i}deg`, "--d": `${60 + (i % 3) * 22}px` }}></i>`)}
    <div class="burst-check">✓<span>${label}</span></div>
  </div>`;
}

/** Card that tilts slightly in 3D toward the pointer. */
export function Tilt({ children, className = "", max = 5 }) {
  const ref = useRef(null);
  function move(e) {
    if (reduced() || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
    ref.current.style.transform = `perspective(900px) rotateX(${(-y * max).toFixed(2)}deg) rotateY(${(x * max).toFixed(2)}deg) translateZ(0)`;
  }
  function leave() { if (ref.current) ref.current.style.transform = ""; }
  return html`<div ref=${ref} class=${"tilt " + className} onMouseMove=${move} onMouseLeave=${leave}>${children}</div>`;
}
