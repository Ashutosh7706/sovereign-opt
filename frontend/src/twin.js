// Digital Twin tab — 3D refinery driven live by the solver.

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

  // Load layout
  useEffect(() => {
    let cancelled = false;

    setErr("");
    setLayout(null);
    setFinal(null);
    setObjective(null);
    setIter(null);
    setNodesState(null);
    setPhase("loading refinery layout...");

    api(`/api/twin/layout?stage=${encodeURIComponent(stage)}`)
      .then((data) => {
        if (!cancelled) {
          setLayout(data);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setErr(error?.message || "Unable to load refinery layout.");
          setPhase("unable to load layout");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [stage]);

  // Create 3D engine
  useEffect(() => {
    if (!layout || !box.current || !gl) {
      return;
    }

    if (engine.current) {
      engine.current.dispose();
      engine.current = null;
    }

    const currentBox = box.current;

    engine.current = new Refinery3D(currentBox, layout, {
      onSelect: setSel,
    });

    engine.current.autoRotate = rotate;

    const ro = new ResizeObserver(() => {
      if (engine.current) {
        engine.current.resize();
      }
    });

    ro.observe(currentBox);

    setSel(null);

    // Automatically solve when layout is loaded
    run();

    return () => {
      ro.disconnect();

      if (engine.current) {
        engine.current.dispose();
        engine.current = null;
      }
    };
  }, [layout]);

  // Auto rotation
  useEffect(() => {
    if (engine.current) {
      engine.current.autoRotate = rotate;
    }
  }, [rotate]);

  // Cleanup WebSocket
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  // Apply solver state
  function apply(state) {
    if (engine.current && state) {
      engine.current.setState(state);
    }

    if (state?.nodes) {
      setNodesState(state.nodes);
    }
  }

  // Start solver
  function run() {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    setErr("");
    setFinal(null);
    setRunning(true);
    setIter(null);
    setObjective(null);
    setNodesState(null);
    setPhase("connecting...");

    const protocol =
      location.protocol === "https:" ? "wss" : "ws";

    const ws = new WebSocket(
      `${protocol}://${location.host}/ws/twin`
    );

    wsRef.current = ws;

    let done = false;

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          stage,
        })
      );
    };

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) {
        return;
      }

      let message;

      try {
        message = JSON.parse(event.data);
      } catch {
        setErr("Received invalid data from the solver.");
        return;
      }

      if (message.type === "layout") {
        setPhase(
          "solving: interior-point iterations on the LP relaxation"
        );
        return;
      }

      if (message.type === "iter") {
        setIter(message);

        setPhase(
          `${message.phase} · iteration ${message.iter}`
        );

        if (message.objective != null) {
          setObjective(message.objective);
        }

        apply(message.state);
        return;
      }

      if (message.type === "incumbent") {
        setPhase(
          message.phase || "integer solution found"
        );

        if (message.objective != null) {
          setObjective(message.objective);
        }

        apply(message.state);
        return;
      }

      if (message.type === "final") {
        done = true;

        setFinal(message);
        setRunning(false);

        if (message.objective != null) {
          setObjective(message.objective);
        }

        if (message.status === "optimal") {
          setPhase(
            "optimal plan - colours show exact shadow prices"
          );
        } else {
          setPhase(
            `solver status: ${message.status || "unknown"}`
          );
        }

        apply(message.state);

        ws.close();
        return;
      }

      if (message.type === "error") {
        done = true;

        setErr(
          message.message || "Solver returned an error."
        );

        setRunning(false);
        setPhase("solver error");

        ws.close();
      }
    };

    ws.onerror = () => {
      if (wsRef.current !== ws) {
        return;
      }

      setErr(
        "Unable to connect to the digital-twin solver."
      );

      setRunning(false);
      setPhase("connection error");
    };

    ws.onclose = () => {
      if (wsRef.current !== ws) {
        return;
      }

      wsRef.current = null;
      setRunning(false);

      if (!done) {
        setErr(
          "Connection dropped - press Solve & watch to retry."
        );

        setPhase("connection closed");
      }
    };
  }

  // Thresholds
  const thresholds = layout?.thresholds || {
    amber: 2,
    red: 8,
  };

  // Bottlenecks
  const bottlenecks = nodesState
    ? Object.entries(nodesState)
        .filter(
          ([, state]) =>
            Number(state?.dual || 0) > 1e-6
        )
        .sort(
          (a, b) =>
            Number(b[1]?.dual || 0) -
            Number(a[1]?.dual || 0)
        )
        .slice(0, 6)
    : [];

  const labelOf = (id) => {
    const node = layout?.nodes?.find(
      (item) => item.id === id
    );

    return node?.label || id;
  };

  return html`
    <div class="grid">

      <section class="panel twin-panel">

        <div class="row">

          <h2 style=${{ margin: 0 }}>
            3D digital twin · live from the solver
          </h2>

          <span class="spacer"></span>

          <label class="sr-only" for="stg">
            plan
          </label>

          <select
            id="stg"
            value=${stage}
            onChange=${(event) =>
              setStage(event.target.value)}
            disabled=${running}
          >
            <option value="production">
              Production plan
            </option>

            <option value="shadow">
              Shadow plan (with trial constraints)
            </option>
          </select>

          <label class="chk">

            <input
              type="checkbox"
              checked=${rotate}
              onChange=${(event) =>
                setRotate(event.target.checked)}
            />

            auto-rotate

          </label>

          <button
            class="btn"
            onClick=${() =>
              engine.current &&
              engine.current.resetCamera()}
          >
            Reset view
          </button>

          <button
            class="btn primary"
            onClick=${run}
            disabled=${running || !gl}
          >
            ${running ? "Solving..." : "Solve & watch"}
          </button>

        </div>

        <div class="twin-status">

          <div class="stat">

            <span class="l">
              Phase
            </span>

            <span class="phase">
              ${phase}
            </span>

          </div>

          <div class="stat">

            <span class="l">
              Gross margin ($k/day)
            </span>

            <${Ticker}
              value=${objective}
              digits=${1}
              className="big"
            />

          </div>

          <div class="stat">

            <span class="l">
              μ (complementarity)
            </span>

            <span class="num">

              ${
                final
                  ? final.status === "optimal"
                    ? "converged"
                    : "—"
                  : iter
                    ? Number(
                        iter.mu || 0
                      ).toExponential(2)
                    : "—"
              }

            </span>

          </div>

          <div class="stat">

            <span class="l">
              Compute
            </span>

            <span class="num small">

              ${
                final
                  ? `${String(
                      final.device || "cpu"
                    ).toUpperCase()} · ${secs(
                      final.seconds
                    )}`
                  : running
                    ? "..."
                    : "—"
              }

            </span>

          </div>

        </div>

        ${
          err &&
          html`
            <p class="err">
              ${err}
            </p>
          `
        }

        ${
          !gl
            ? html`
                <div class="note bad">

                  This browser has WebGL turned off,
                  so the 3D view cannot render.
                  The plan itself is available from
                  the Refinery plan tab.

                </div>
              `
            : html`

                <div class="twin-stage">

                  <div
                    ref=${box}
                    class="twin-canvas"
                    aria-label="3D refinery view"
                  ></div>

                  <${Burst}
                    fire=${
                      final?.status === "optimal"
                        ? final.replay_id
                        : null
                    }
                    label="OPTIMAL PLAN"
                  />

                  ${
                    sel &&
                    html`

                      <div
                        class="twin-card"
                        style=${{
                          borderColor: sel.color,
                        }}
                        role="dialog"
                        aria-label=${sel.label}
                      >

                        <button
                          class="x"
                          onClick=${() =>
                            setSel(null)}
                          aria-label="close"
                        >
                          ×
                        </button>

                        <b>
                          ${sel.label}
                        </b>

                        <div class="kvs">

                          <span>
                            Level
                          </span>

                          <span class="num">

                            ${
                              (
                                Number(
                                  sel.target || 0
                                ) * 100
                              ).toFixed(1)
                            }%

                            ${
                              sel.capacity
                                ? ` of ${fmt(
                                    sel.capacity,
                                    0
                                  )} kbbl/d`
                                : ""
                            }

                          </span>

                          <span>

                            ${
                              sel.kind ===
                              "product"
                                ? "Sales"
                                : sel.kind === "crude"
                                  ? "Crude run"
                                  : "Feed"
                            }

                          </span>

                          <span class="num">

                            ${fmt(
                              sel.value,
                              1
                            )}
                            kbbl/d

                          </span>

                          ${
                            sel.kind === "crude" &&
                            html`

                              <span>
                                Parcels bought
                              </span>

                              <span class="num">
                                ${sel.parcels}
                              </span>

                            `
                          }

                          ${
                            sel.kind === "unit" &&
                            html`

                              <span>
                                State
                              </span>

                              <span>

                                ${
                                  sel.on
                                    ? "running"
                                    : "off"
                                }

                              </span>

                            `
                          }

                          <span>
                            In / out
                          </span>

                          <span class="num">

                            ${fmt(
                              sel.inflow,
                              1
                            )}
                            /
                            ${fmt(
                              sel.outflow,
                              1
                            )}
                            kbbl/d

                          </span>

                          <span>
                            Shadow price
                          </span>

                          <span
                            class="num"
                            style=${{
                              color: sel.color,
                            }}
                          >

                            $${fmt(
                              sel.dual,
                              2
                            )}/bbl

                            ${
                              sel.bottleneck
                                ? " (bottleneck)"
                                : ""
                            }

                          </span>

                        </div>

                        ${
                          sel.binding &&
                          html`

                            <p class="sub">
                              Binding:
                              ${sel.binding}
                            </p>

                          `
                        }

                        ${
                          Array.isArray(
                            sel.quality
                          ) &&
                          sel.quality.length > 0 &&
                          html`

                            <p class="sub">

                              Quality-limited:
                              ${sel.quality.join(
                                ", "
                              )}

                            </p>

                          `
                        }

                        ${
                          Number(
                            sel.dual || 0
                          ) > 1e-6 &&
                          html`

                            <p class="sub">

                              One more kbbl/d
                              of this limit is
                              worth ≈
                              $${fmt(
                                Number(sel.dual) *
                                  1000,
                                0
                              )}/day of margin.

                            </p>

                          `
                        }

                      </div>

                    `
                  }

                  <div class="twin-legend">

                    <span>

                      <i
                        style=${{
                          background:
                            "#00e5ff",
                        }}
                      ></i>

                      slack (y*=0)

                    </span>

                    <span>

                      <i
                        style=${{
                          background:
                            "#00ff9d",
                        }}
                      ></i>

                      active

                    </span>

                    <span>

                      <i
                        style=${{
                          background:
                            "#ffb300",
                        }}
                      ></i>

                      bottleneck &gt;
                      $${thresholds.amber}/bbl

                    </span>

                    <span>

                      <i
                        style=${{
                          background:
                            "#ff1744",
                        }}
                      ></i>

                      severe ≥
                      $${thresholds.red}/bbl

                    </span>

                    <span class="hint">

                      drag = rotate ·
                      scroll = zoom ·
                      click = details

                    </span>

                  </div>

                </div>

              `
        }

      </section>

      <div class="grid g-side">

        <section class="panel">

          <h2>
            Where the plan is constrained
          </h2>

          ${
            bottlenecks.length === 0
              ? html`

                  <div class="empty">

                    ${
                      running
                        ? "Shadow prices appear as the solver converges..."
                        : "No binding limit yet"
                    }

                  </div>

                `
              : html`

                  <table>

                    <thead>

                      <tr>
                        <th>
                          unit / tank
                        </th>

                        <th class="r">
                          shadow price
                        </th>

                        <th>
                          meaning
                        </th>
                      </tr>

                    </thead>

                    <tbody>

                      ${
                        bottlenecks.map(
                          ([id, state]) =>
                            html`

                              <tr
                                key=${id}
                                class="clickable"
                                onClick=${() => {

                                  if (
                                    !engine.current
                                  ) {
                                    return;
                                  }

                                  engine.current.selected =
                                    id;

                                  setSel(
                                    engine.current.describe(
                                      id
                                    )
                                  );

                                }}
                              >

                                <td>
                                  ${labelOf(id)}
                                </td>

                                <td
                                  class="r num"
                                  style=${{
                                    color:
                                      Number(
                                        state.dual ||
                                          0
                                      ) >=
                                      thresholds.red
                                        ? "var(--bad)"
                                        : Number(
                                              state.dual ||
                                                0
                                            ) >
                                            thresholds.amber
                                          ? "var(--warn)"
                                          : "var(--ok)",
                                  }}
                                >

                                  $${fmt(
                                    state.dual,
                                    2
                                  )}/bbl

                                </td>

                                <td class="sub">

                                  ${
                                    state.binding ||
                                    "active"
                                  }

                                </td>

                              </tr>

                            `
                        )
                      }

                    </tbody>

                  </table>

                `
          }

          <p
            class="sub"
            style=${{
              marginTop: 10,
            }}
          >

            During the solve, levels and colours
            come from the live interior-point
            iterate, then from each integer plan
            found, then from the final optimal plan
            with exact duals.

            ${
              final
                ? ` Replay bundle ${final.replay_id}.`
                : ""
            }

          </p>

        </section>

        <${TelemetryPanel}
          compact=${true}
        />

      </div>

    </div>
  `;
}