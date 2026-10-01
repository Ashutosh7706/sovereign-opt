# Data flow: what leaves the box (roadmap #119, for sovereignty / CVC review)

```
                         PLANT FIREWALL (nothing crosses it in modes 1 and 2)
 ┌──────────────────────────────────────────────────────────────────────────────────────────┐
 │  Operator browser ──HTTPS──▶ Sovereign Optimizer (single host / pod)                       │
 │   (vendored JS,             ├─ solver: IPM / HSD / B&B  (CPU FP64 or local GPU)          │
 │    no CDN)                  ├─ NL gate: offline rule parser                               │
 │                             ├─ SQLite: users, audit chain, plans, replay bundles          │
 │                             └─▶ anchor dir (USB / WORM share)  ◀─ chain tip hashes only   │
 │                                                                                          │
 │  Mode 2 only:  NL gate ──HTTP(loopback / on-prem LAN)──▶ open-weight LLM server (Ollama)   │
 │                sends: the one sentence + plant vocabulary (product/unit/crude names, specs) │
 │  Optional:     alert webhook ──▶ on-prem monitoring  (failure counts, no plant data)       │
 └──────────────────────────────────────────────────────────────────────────────────────────┘
          │  Mode 3 only (SKU-1 Enterprise builds; impossible in SKU-2)
          ▼
   api.anthropic.com  ◀── the one sentence + plant vocabulary (no plan, no prices, no volumes)
```

| NL compiler mode | Build | Leaves the host | Leaves the plant | Recorded |
|---|---|---|---|---|
| 1 · sovereign-rules (default) | SKU-1, SKU-2 | nothing | nothing | parser `rules:<version>` in every audit entry |
| 2 · sovereign-local-llm | SKU-1, SKU-2 | sentence + vocabulary to the on-prem LLM. Loopback only unless `SOVEREIGN_ALLOW_LAN_LLM=1` | nothing | `local:<model>`, plus a `sovereignty.relaxed` event if the LAN host is allowed |
| 3 · cloud-llm | **SKU-1 only** | sentence + vocabulary | yes, to Anthropic | `cloud:<model>`; UI badge "egress: cloud LLM"; `settings.changed` event |

Never sent anywhere in any mode:
- plans, flows or volumes;
- crude or product prices and margins;
- the audit log;
- user accounts;
- replay bundles;
- solver matrices.

There is no licence server, no telemetry and no update check.
