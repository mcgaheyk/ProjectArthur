# ProjectArthur on a standalone Ulanzi TC002

**Technical proposal for independent review · November 20, 2026**  
**Decision requested:** Is a short hardware feasibility test justified, and what would make us stop?  
**Baseline:** ProjectArthur commit `1db0e56e712ed6771e2df93ceb9f90efaba00bb0`, AWTRIX NG 1.2.3, Litter-Robot 4. This document describes proposed work. Hardware measurements and results below are attributed to their original setups.

## Recommendation

Test a small native, read-only Whisker client on the TC002 before porting the display. First establish that it can fit, start unattended, renew its session and receive a real visit while AWTRIX runs. If those tests pass, keep AWTRIX as the display driver and port the existing bitmap renderer into the companion. This gives one process responsibility for deciding and drawing a frame, preserves the existing display API and lets the Python studio serve as a reference. If deployment requires maintaining a custom firmware build, price that maintenance against running the existing feeder on a Pi before committing to the port.

The finished clock should work on USB power and home Wi-Fi with the Mac shut down. It still uses Whisker’s cloud and Open-Meteo. The Mac can build, provision, update and diagnose the clock. PETLIBRO, a cloud relay, a home MQTT broker and remote control of the robot are outside this proposal.

## Evidence and constraints

| Evidence | Design consequence |
| --- | --- |
| The current Mac feeder draws the full 52×16 image. It ticks at 8 Hz, pushes changed frames, and refreshes a static frame every 30 seconds. The real clock has matched a reference frame on all 832 pixels. | Reuse the existing bitmap contract. Check both transitions and individual pixels against the Python implementation. |
| Seven recorded visits produced a weight update 48–86 seconds after detection. The robot started cleaning about eight minutes after the weight update on this configuration. | Subscribe to live events. Five-minute polling alone can miss the entire occupied interval. Read the robot’s configured delay when deriving the timer. |
| AWTRIX runs on ARMv7 Linux. The developer guide describes a read-only release image in an 8 MiB partition shared with other content, and a separate 8 MiB data partition. | Neither partition size is the space available for a new executable. Verify the pinned release layout, real free space, update reserve and boot mechanism before selecting a language or installer. [AWTRIX developer guide](https://ang.blueforcer.de/tc002/developers/tc002/) |
| Another TC002 owner measured about 35 MiB available to Linux in total, and roughly 12–16 MiB available with two other runtimes. | These figures justify measuring this particular clock under AWTRIX. They cannot establish whether Python or Go fits. [Hardware observations](https://github.com/atomicstack/tc002-customisation) |
| AWTRIX supports persistent Berry apps and outbound HTTPS. Its public scripting network guide does not establish an arbitrary outbound WebSocket client for Berry on 1.2.3. A Go client documents LR4 GraphQL queries and live subscriptions. | Start with a native-client experiment. Verify the exact client revision, authentication, reconnects and activity-history coverage before adopting it. [Berry networking](https://blueforcer.github.io/awtrix-ng/tc002/guides/scripting/network/) · [Go LR4 package](https://pkg.go.dev/github.com/ic2hrmk/golitterbot/robot/litterrobot4) |

### Alternatives worth reviewing

| Route | Decision criterion |
| --- | --- |
| Existing Python feeder on the clock | Best reuse if a compatible Python 3.11+ runtime, aiohttp, pylitterbot and its AWS dependencies demonstrably fit under peak load. Inspect available packages and estimate the complete installation first. |
| Native client and renderer alongside AWTRIX | Preferred prototype. It preserves the display pipeline. Its main liabilities are installation, runtime footprint and behavioral drift from Python. |
| Berry-only app | Reconsider if the reviewer can identify a version-pinned, supported way to authenticate, refresh and receive live LR4 events. Rendering and HTTPS alone cover only part of the requirement. |
| Pi running the existing feeder | Working architecture with the smallest application change. Choose this if clock-only deployment, resource limits or ongoing firmware upkeep outweigh eliminating the extra device. |

## Phase 1 — Establish a reversible deployment path

1. Record firmware version and settings, app rotation, baseline display responsiveness, available RAM under idle and animated loads, usable storage and existing recovery options. Use documented interfaces and owner-authorized device access. Record which numbers come from AWTRIX quotas and which come from the Linux system.
2. Inspect the **1.2.3** installer, loader and supervisor. Identify exactly how a companion would launch at boot, where its executable and private state would live, how to stop it, and what a normal AWTRIX update would overwrite. The documented supervisor launches AWTRIX itself. A supported third-party autostart slot remains unverified. Uploading a Berry app and installing a Linux executable are separate capabilities. [Pinned source](https://github.com/Blueforcer/awtrix-ng/tree/v1.2.3)
3. Build a minimal ARMv7 candidate on the Mac or a build host. Measure the compressed image delta, installed footprint, certificate bundle and peak RAM during TLS connection. Test installation and recovery with a harmless status display before introducing an account. Where a temporary install is possible, use that first.
4. **Pass** when the candidate fits alongside the full release and its update reserve, starts with the Mac disconnected, survives a cold boot, and can be removed so the Mac feeder works again. Record actual margins. **Pause** if the only viable route rewrites a firmware image, needs undocumented privileged access, or lacks a demonstrated recovery path. Review that cost separately before touching the working installation.

## Phase 2 — Prove the Whisker connection

Pin the candidate client and compare its LR4 data against the Python reference. Required operations are login, token refresh, selection of the intended robot, an initial snapshot, an event subscription, a periodic fresh read and activity-history queries. Verify actual responses for status, display code, pet weight, cycle count, configured delay, online/power flags and drawer/litter levels. Check how omitted fields in partial updates are merged. Exercise the client against sanitized fixtures before the live trial.

Provision account material locally into a private store outside AWTRIX’s script, file-serving and backup surfaces. Verify where the client writes refreshed tokens and that diagnostics redact them. Use certificate verification and wait for a usable system clock before authenticating. Keep writes bounded. A boot log, a status counter and a small rotating diagnostic log are sufficient for the experiment.

Stop the Mac feeder for the live trial. Run one read-only client against the account. Use push events as the primary signal and keep the existing five-minute poll as a backstop. Compare a complete real visit with the Whisker activity timeline. Record device receive time separately from the robot’s event time. Capture one actual token renewal, a reconnect and a fresh snapshot after interruption. A manual clean exercises the cycle path but cannot validate cat detection or departure.

**Pass** when one real visit produces detection, weight, cleaning and ready in order, renewal works unattended, and a lost session recovers without restarting from the Mac. Collect peak and minimum available memory during login, reconnect, history fetch and animation together. Fail the trial for process eviction, repeated restarts, unusable controls, or a stale healthy display. If evidence is missing, report the outcome as inconclusive.

## Phase 3 — Port behavior and pixels

| Component | Proposed contract and verification |
| --- | --- |
| Whisker source and tracker | Normalize events into a snapshot with last successful contact, status transition, last visit and observed departure. A local heartbeat cannot advance cloud freshness. Rebuild from history at startup, after a contact gap over ten minutes, after an unexplained cycle, and on the existing six-hour schedule. Preserve the missing-weight fallback and avoid inventing a departure timestamp. |
| Mapping | Use the existing state priorities, including unavailable data, robot offline/off, faults, full drawer, interrupted, waiting, cleaning and idle. After 120 seconds without a weight reading, the current fallback can show waiting with no countdown. Cleaning progress is a 130-second estimate. Only the robot’s transition to ready ends the cycle. |
| Display | Retain the artwork, fonts, captions, palette, configured timings and single 52×16 layout. Build assets/config from the existing source files. Target the same local AWTRIX HTTP bitmap interface and verify loopback access on the device. One RGB frame is 2,496 bytes before encoding. Push changed frames at the existing 8 Hz tick and re-register the app after an AWTRIX restart. |
| Two independent failure signals | A known Whisker error immediately produces NO DATA. A seemingly healthy connection with no successful contact ages out after 600 seconds. If the companion itself dies, AWTRIX’s existing 90-second frame expiry marks its last frame stale. Verify all three paths on hardware. |
| Weather and time | Read Boston weather every 900 seconds. After 2,700 seconds discard the stale reading and use the idle home fallback. Use America/New_York for displayed time, date and night behavior. Verify timezone data or equivalent DST support on the target. Use monotonic time for retry delays. |
| Scheduling | Preserve the current three-minute idle round, with home, weather/date and animated scenes. A robot event must preempt any idle page at the next render tick after processing. Weather requests and token refresh must allow drawing to continue. |

Use the same synthetic snapshot, config, clock time and animation tick in both renderers. Compare all 832 RGB pixels on representative frames for every state and at transitions. Replay the sanitized event timeline through both trackers and compare resulting states and countdowns. Include a missing weight, a short false detection, sleep mode, a restart mid-visit, an interruption, and a missed visit recovered from history. Keep differences visible for review.

## Phase 4 — Acceptance and rollback

| Trial | Evidence required |
| --- | --- |
| 48 hours with the Mac feeder stopped | Timestamped transitions, at least one complete real visit, successful renewal, stable RAM/storage, no unexplained restarts, and usable clock controls. Extend the trial if no visit or renewal occurs. |
| Wi-Fi outage and recovery | NO DATA follows the existing failure rules. Current status and last visit recover automatically. Use replay for a visit missed during an outage if one does not occur naturally, and label that result as replay evidence. |
| Cold boot and process failure | Clock joins Wi-Fi, obtains valid time, logs in and registers the display app on its own. Terminating only the companion demonstrates expiry or recovery. Restarting AWTRIX alone demonstrates that the companion restores the display. |
| Display parity and responsiveness | Golden frames match, or each deliberate difference is accepted. Compare event receipt to first correct frame separately from upstream cloud latency. At 8 Hz a tick is 125 ms. Measure worst observed local delay during weather and history fetches. |
| Rollback | Stop and disable the companion first. Restore the known AWTRIX setup through the rehearsed path, remove test account material, then start the Mac feeder. Verify one live snapshot, correct app rotation and a fresh frame. |

Cut over only after the trials pass. Retain the Mac/Pi feeder and studio as a reference and fallback. Record the tested firmware/client versions, measured margins and recovery procedure alongside the outcome.

## Independent reviewer brief

Return **proceed to prototype**, **revise the design**, or **use the Pi**. Separate blockers for the initial experiment from blockers for permanent use. For each blocker, cite a pinned source or name the smallest measurement that would resolve it. In particular:

1. Is there a documented, reversible launch path for an extra Linux process on AWTRIX NG 1.2.3? What survives an update?
2. Does the proposed client cover authenticated push, refresh and history on this LR4? Which observed fields could be lost in translation?
3. Can the complete package fit with adequate margin during the worst simultaneous workload? What evidence would change the choice between Python and native?
4. Could the display show a believable but stale state after any single process or network failure?
5. Is there a simpler clock-only route that preserves short visits, current graphics and unattended recovery?

### Project references

The repository paths below are relative to the [reviewed baseline](https://github.com/mcgaheyk/ProjectArthur/tree/1db0e56e712ed6771e2df93ceb9f90efaba00bb0). The document is self-contained if that revision is unavailable to the reviewer.

- `docs/STATUS.md`, `docs/DECISIONS.md`, `docs/states.md` — recorded observations, architecture decisions and state definitions.
- `docs/reference/pylitterbot.md`, `arthur/sources/whisker.py`, `arthur/sources/tracker.py` — authentication, event semantics and history reconciliation.
- `config/display.toml`, `arthur/mapping.py`, `arthur/schedule.py`, `arthur/render/` — behavioral and visual reference.
- `docs/reference/awtrix-ng.md`, `arthur/pusher.py`, `arthur/transports/awtrix_ng.py` — tested device API, timing, expiry and rotation.
