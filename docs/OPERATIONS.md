# Operations runbook

Run all `manage.py` commands from `backend/`. They use the same `SOVEREIGN_*` settings as the server.

## Install options
| Target | How |
|---|---|
| Single Windows/Linux host | Verify the signed release (below). Then `run.ps1 -Https` or `./run.sh --https`, or systemd (`deploy/systemd/`, hardened unit + health watchdog timer) |
| Docker | `docker build -t sovereign-optimizer:0.3.0 .` → `docker save` → carry across the air gap → `docker load` → `docker run -p 8000:8000 -v sovdata:/data ...` |
| Kubernetes | `deploy/helm/sovereign-optimizer`. Its NetworkPolicy **denies all egress** except cluster DNS, so the air gap is enforced by the network, not only by configuration |
| GPU | `Dockerfile.cuda` + NVIDIA Container Toolkit, or `pip install cupy-cuda12x` on the host. The boot log states which compute path runs |

On first boot, user `admin` is created with a one-time PIN (console + `data/BOOTSTRAP_ADMIN.txt`).
Create named users, change the admin PIN, then delete the file.

## Backup and retention (roadmap #32, #35)
```
python manage.py backup --keep 30                  # consistent online copy, safe while running
python manage.py backup --dest /mnt/backup --keep 90
```
Schedule it daily with cron or a systemd timer, and keep copies off-box.
Suggested retention:
- Database backups: 90 daily.
- Audit exports (`export-audit`): for the plant's statutory record period, typically 7+ years.

The audit chain itself is **never pruned**. It is a legal ledger and grows by about 1 KB per event.

Restore: stop the service, copy a backup over `data/sovereign.db`, start, then run `python manage.py verify-audit`.

## External anchoring of the audit chain (#36)
Set `SOVEREIGN_ANCHOR_DIR` to storage that a local admin cannot rewrite: a WORM share, a USB key
kept by the shift supervisor, or a directory synced to a separate security domain. Then anchor at
least once per shift:
```
python manage.py anchor            # or the "Anchor chain tip" button (supervisor)
```
Also print the anchor line in the shift report. `verify-audit` and `/health` compare the chain
against every anchor. A rewritten history no longer matches.

## Availability (#37)
This is a single-writer design (SQLite). Resilience comes from:
- automatic restart (systemd `Restart=always`, Kubernetes liveness probe);
- the `/health` watchdog timer, which restarts a hung process;
- a persistent volume;
- daily backups.

Expected recovery after a crash is under 30 s. Active-active HA needs a server database and is on
the roadmap. In-flight solves are drained on shutdown (up to 30 s).

## Monitoring
- `/health` returns `ok` or `degraded`, with reasons: database, audit chain + anchors, disk, compute path, GPU fallbacks, solver failure alert, LLM state.
- `/metrics` exposes Prometheus metrics.
- Logs are one JSON object per line on stdout.
- Alerts fire after `SOVEREIGN_ALERT_THRESHOLD` consecutive solver failures or ill-conditioned solves. They produce an ERROR log, an audit event, `/health` degraded, and an optional on-prem webhook.

## Secrets (#27)
- An SKU-2 build holds **no external secrets**: no licence server, no API keys.
- User PINs are stored only as scrypt hashes.
- The TLS private key (`certs/server.key`, mode 0600) is the main secret.
- For SKU-1 cloud mode, provide `ANTHROPIC_API_KEY` through the service environment file (`/etc/sovereign-optimizer.env`, mode 0600) and **never** commit it. `.env`, `keys/` and `certs/` are git-ignored.
- Rotation:
  - TLS: re-run `make_cert.py` (or re-issue from the plant CA) yearly, then restart.
  - API keys: rotate in the provider console, update the env file, restart.
  - User PINs: `manage.py reset-pin NAME`.

## Updates on an air-gapped site (#106)
The vendor builds `release.py build --key <offline signing key>`. The site receives:
- the zip;
- `SHA256SUMS`;
- `SHA256SUMS.sig`;
- once, out of band, the vendor public key.

On the site:
```
python backend/scripts/release.py verify sovereign-optimizer-X.zip --pub vendor_release.pub
python backend/manage.py backup --keep 90           # before every upgrade
```
Then unpack next to the old version, copy `backend/data`, start, run `manage.py verify-audit`, and
check `/health`. Database migrations apply automatically and are idempotent. To roll back, restore
the backup and the previous release directory.

## Configuration check (#83, #109)
`python manage.py config` validates and prints every variable. The server refuses to start on an
invalid value (for example cloud mode enabled in an SKU-2 build).

## Full data purge / uninstall (#111)
```
python manage.py export-audit /secure/archive/audit-final.jsonl   # if your retention rules require it
python manage.py purge --yes
```
Also delete any off-box backups and anchors according to the CVC and site retention policy, then
remove the install directory (and Docker volumes or PVCs).
