// Live hardware telemetry panel (dashboard upgrade plan, feature 6): real nvidia-smi readings and
// the solver process's own CPU load, streamed over /ws/telemetry every 0.5 s.
import { html, useState, useEffect, useRef } from "./lib.js";
import { Gauge, Sparkline } from "./fx.js";

export function useTelemetry(enabled = true) {
  const [t, setT] = useState(null);
  const [hist, setHist] = useState([]);
  const [conn, setConn] = useState("connecting");
  const wsRef = useRef(null);
  useEffect(() => {
    if (!enabled) return;
    let stop = false, retry = 0;
    const open = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws/telemetry`);
      wsRef.current = ws;
      ws.onopen = () => { setConn("live"); retry = 0; };
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data);
        setT(m);
        setHist((h) => [...h.slice(-119), m]);
      };
      ws.onclose = () => {
        if (stop) return;
        setConn("reconnecting");
        retry = Math.min(retry + 1, 5);
        setTimeout(open, 800 * retry);
      };
    };
    open();
    return () => { stop = true; wsRef.current && wsRef.current.close(); };
  }, [enabled]);
  return { t, hist, conn };
}

export function TelemetryPanel({ compact = false }) {
  const { t, hist, conn } = useTelemetry(true);
  const gpu = t && t.gpu_available;
  const vramPct = gpu && t.vram_total_mb ? (100 * t.vram_used_mb) / t.vram_total_mb : null;
  return html`<section class=${"panel telemetry" + (compact ? " compact" : "")}>
    <div class="row"><h2 style=${{ margin: 0 }}>Live hardware</h2><span class="spacer"></span>
      <span class=${"chip " + (conn === "live" ? "ok" : "warn")}>${conn === "live" ? "● live 2 Hz" : conn}</span></div>
    <p class="sub">${gpu ? `${t.gpu_name || "NVIDIA GPU"} · real nvidia-smi telemetry` :
      (t && t.gpu_note) || "waiting for data…"}</p>
    <div class="gauges">
      <${Gauge} label="GPU load" value=${gpu ? t.gpu_util : null} color="var(--s3)"
        sub=${gpu ? html`<${Sparkline} points=${hist.map((h) => h.gpu_util)} max=${100} color="var(--s3)" />` : "no GPU"} />
      <${Gauge} label="VRAM" value=${vramPct} color="var(--s2)"
        sub=${gpu ? `${Math.round(t.vram_used_mb)} / ${Math.round(t.vram_total_mb)} MB` : "—"} />
      <${Gauge} label="GPU temp" value=${gpu ? t.temp_c : null} max=${100} unit="°C" color="var(--warn)"
        sub=${gpu ? `${t.sm_clock_mhz ?? "—"} MHz${t.power_w != null ? ` · ${t.power_w.toFixed(0)} W` : ""}` : "—"} />
      <${Gauge} label="Solver CPU" value=${t ? t.solver_cpu_pct : null} color="var(--accent)"
        sub=${t ? html`<${Sparkline} points=${hist.map((h) => h.solver_cpu_pct)} max=${100} />` : ""} />
    </div>
    ${!compact && html`<p class="sub">Solver CPU = this server process's share of all ${t ? t.cpu_threads : "…"} CPU threads.
      ${t && t.gpu_fallbacks ? ` · GPU→CPU fallbacks so far: ${t.gpu_fallbacks}` : ""}</p>`}
  </section>`;
}
