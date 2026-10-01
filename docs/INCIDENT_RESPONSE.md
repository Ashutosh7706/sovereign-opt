# Incident-response runbook (roadmap #117)

## A. "AUDIT CHAIN BROKEN" or an anchor mismatch
1. **Freeze.** Stop acting on new recommendations. Advisory mode means the plant keeps running on its existing procedures. Tell the shift supervisor and IT security.
2. **Preserve.** Copy the whole `data/` directory (including the `-wal` and `-shm` files) to write-once media **before** restarting anything. Record the time and the `/health` output.
3. **Locate.** Run `python manage.py verify-audit`. It reports the first bad entry (`bad_seq`) and the reason:
   - *content hash mismatch*: a row was edited.
   - *sequence gap*: a row was deleted.
   - *anchor mismatch*: the whole chain was regenerated.
4. **Compare.** Restore the latest pre-incident backup to a separate machine and `verify-audit` it. Diff its entries against the damaged copy from the first common entry onwards. Off-box anchors show which chain tip is genuine.
5. **Decide.** Rebuild from the last verified backup. Re-enter any legitimate decisions made after it, as new entries by named people. Never "repair" old entries.
6. **Report.** Record the timeline, the affected entries and the root cause. Revoke and reset the credentials of any account involved (`manage.py reset-pin`, disable the user). Rotate the TLS key if the host was compromised.

## B. A recommendation turned out to be wrong or unsafe
1. **Contain.** A supervisor retires the constraint or constraints involved (Refinery plan tab). If the plan itself is suspect, stop using the tool and revert to the previous planning method.
2. **Reconstruct.**
   - The audit log shows who proposed and approved which constraint, and when. It includes the original English text, the parsed IR, the parser version, the generated solver rows and the confidence score.
   - The plan's **replay id** (printed on the handover sheet) identifies the exact model and solution.
3. **Replay.** On the Audit tab, replay that bundle. A **bit-exact match** proves the software computed exactly what was shown, so the cause is in the inputs or the model. A mismatch points to a software or environment defect: freeze the version and escalate to the vendor.
4. **Classify the root cause:**
   - wrong input data;
   - misparsed text, which becomes a new red-team case in `tests/nlc_eval.jsonl`;
   - a modelling gap in the refinery model;
   - a solver defect, which becomes a regression test.
5. **Fix forward.** Add the regression or red-team case so the build fails if it ever happens again. Record the incident in the CHANGELOG under "Fixed".

## C. Repeated solver failures or ill-conditioning alerts
Check `/health` → `solver_alert`, then the failing models in the replay list. Replay locally with
`SOVEREIGN_DISABLE_GPU=1` to rule out the GPU path. Attach the replay bundle when escalating.

## D. Suspected data exfiltration
In an SKU-2 build no egress path exists. Confirm with:
- `/api/status` → `egress: none`;
- the Kubernetes NetworkPolicy, or the host firewall rules;
- the audit log for any `sovereignty.relaxed` or `settings.changed` events.

Any contrary finding is a security incident. Follow A.1–A.2 and involve the CISO.
