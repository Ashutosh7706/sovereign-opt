# Security model

## Controls (roadmap items 21–30, 99)
| Threat | Control |
|---|---|
| Someone signs off as someone else | Per-user PIN (scrypt, per-user salt). Identity comes from the server session, never from request bodies. Every action is audited with the username and role |
| Operator approves a safety change alone | RBAC: safety-tagged constraints and shadow→production promotion need a **supervisor**. Enforced in the API and again in the gate |
| PIN guessing | Lockout after 5 failures (15 min), per-IP login rate limit, constant-time comparison, generic error text |
| Stolen session cookie | HttpOnly, SameSite=Strict, Secure under HTTPS, 12 h lifetime. Only a SHA-256 of the token is stored. Sessions end on PIN change or when the account is disabled |
| Cross-site request forgery | Per-session CSRF token required in the `X-CSRF-Token` header on every write |
| Cross-site WebSocket hijacking | WebSocket needs a valid session **and** a same-origin `Origin` header (close code 4401) |
| Eavesdropping on the LAN | HTTPS (`run -Https`, plant-CA CSR support), HSTS |
| Clickjacking / content injection | `X-Frame-Options: DENY`, CSP `default-src 'self'` (no CDN; all JS vendored), `nosniff`, `no-referrer` |
| Denial of service | Per-user limits on expensive calls, upload size cap (413), MPS parse time budget, bounded NL input length |
| Malformed or hostile MPS files | Parser never crashes: every error becomes `MPSError` (600-case fuzz suite) |
| LLM prompt injection | The LLM can only return a closed typed schema, never math. An offline parser cross-checks it. Instruction-like wording forces human review. The red-team suite gates the build |
| Data leaving the site | SKU-2: cloud LLM mode refused at boot and at runtime, and the SDK is not in the image. Kubernetes NetworkPolicy denies egress. The LAN-LLM relaxation is audited |
| Tampering with history | Hash-chained audit, fail-closed transactional writes, external anchors, bit-exact replay with a fingerprint check |
| Supply chain | Pinned + locked dependencies, SBOM, licence gate, pip-audit in CI, Ed25519-signed releases |

## Known gaps
- No SSO/LDAP yet (local accounts only). Plant AD integration is a pilot-phase item.
- The audit database on the server host is protected by OS permissions. A root/admin attacker can still stop the service or rewrite the database. External anchors make that **detectable**, not impossible.
- FastAPI's `/docs` page loads Swagger UI from a CDN. On an air-gapped site use `/openapi.json`.

## External review (roadmap #121)
Before the next funding conversation, commission a lightweight independent review (typically 5–10 consultant days).

Scope:
1. Authentication, session and CSRF implementation (`auth.py`, `app.py` middleware).
2. The audit chain and anchoring design (`audit.py`, `store.py`).
3. NL-gate bypass attempts (red-team on top of `tests/nlc_eval.jsonl`).
4. Numerical-correctness spot check against a commercial solver on customer-like models.
5. Container / Helm hardening.

Deliverable: a written report for the data room, with findings mapped onto GAP_STATUS.md.

## Reporting a vulnerability
Email the maintainers privately. Do not open a public issue. The acknowledgement target is 3 working days.
