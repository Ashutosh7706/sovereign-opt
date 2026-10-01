# Operator quick-start guide

This guide is for control-room and planning staff; no programming knowledge is needed.

> **The platform only advises.** It never changes a valve, a setpoint or a unit. Every plan it
> shows is a recommendation for people to review.

## 1. Sign in
Open the address your IT team gave you (for example `https://sovereign.plant.local:8000`). Enter
your **username** and **PIN**. Five wrong PINs lock your account for 15 minutes. Ask an admin if
you are locked out.

Your name and role are shown at the top right. Everything you do is recorded under that name.

| Your role | You can |
|---|---|
| Operator | Ask for changes in plain English, approve ordinary ones, reject anything, run plans |
| Supervisor | All of the above, plus approve **safety-related** changes (sulfur, pressure, temperature) and move changes from the trial plan into the real plan |
| Admin | All of the above, plus manage users and settings |

## 2. See today's plan: *Refinery plan* tab
- **Production plan** is the current recommendation: which crudes to buy, which units to run, how much of each product to make, and the gross margin in $ thousand per day.
- **Shadow plan** appears when a change is on trial. It shows the same plan *with* the trial changes, and how much margin they cost or gain.
- **What is binding** lists which limits are costing money. For example, "+10 per +1 kbbl/d" on the crude unit means one more thousand barrels a day of crude capacity would earn about $10k per day more.
- **Print plan** gives a handover sheet with your name, the time and a reference number.

## 3. Ask for a change in plain English: *Constraint compiler* tab
Type one change per line, for example:
- `Tank 3 sulfur limit ≤ 0.2%`
- `FCC shutdown for maintenance`
- `diesel lifting at least 100 kbbl/d`
- `no more than 1 cargo of Basrah`

The system tells you **what it understood** in plain words. Read that sentence carefully: it is
what will be used. Then check the result:
- **APPROVED by policy**: a routine change the system is confident about. It goes into the trial (shadow) plan.
- **PENDING SIGN-OFF**: a person must approve it. This always happens for sulfur, pressure and temperature limits, and when the wording was unclear.
- **BLOCKED**: the change is impossible or outside safe ranges (for example "FCC capacity 8000"). It cannot be approved.
- **NOT UNDERSTOOD**: rephrase it. Give one change, with a number and a unit, and an absolute value ("FCC capacity ≤ 90 kbbl/d", not "increase FCC by 10").

When you press **Approve** you get 5 seconds to press **Undo**. Nothing is recorded until those 5 seconds have passed.

**Warnings to take seriously:**
- *"possible decimal-point or unit slip"*: the new value is far from the current one. For example, did you mean 0.2 or 2.0?
- *"plan becomes INFEASIBLE"*: the plant cannot meet this together with everything else. The message tells you the smallest change that would make it work.

## 4. Trial first, then production
New changes run in the **shadow plan** first. When the trial looks right, a **supervisor** presses
**Promote** to move the change into the production plan. **Retire** removes a change.

## 5. If something looks wrong
- A red banner at the top ("Connection lost", "Service degraded") means tell IT. Do not use the plan until it clears.
- *"AUDIT CHAIN BROKEN"* on the Audit tab: stop and call your supervisor and IT at once (see INCIDENT_RESPONSE.md).
- If a recommendation looks unsafe, **do not act on it**. Reject the change and write the reason in the note. The system keeps everything for review.

## 6. The 3D digital twin (v0.3)
The **3D digital twin** tab draws the refinery and fills it from the solver's answer.
- **Tank height** is how full each unit or tank runs in the plan.
- **Moving dots in the pipes** are the planned flows: faster dots mean more barrels per day.
- **Colour** is what one more barrel of room would be worth:
  - **cyan** = spare capacity;
  - **green** = in use;
  - **amber** = a bottleneck worth more than $2/bbl;
  - **red, pulsing** = a severe bottleneck at $8/bbl or more.
- **Click** anything to see its numbers. **Drag** to turn the view, and **scroll** to zoom.

The colours come from the optimiser's shadow prices. They show where extra capacity would pay off; they are not alarms from the plant.

On the **Benchmark race** tab a blue note may say *"Problem too small for GPU offload — running CPU path"*. That is normal: small plans are faster on the CPU.
