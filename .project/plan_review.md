# Deep Review of `.project/plan.md`

## Executive Verdict

The plan is strong and implementation-oriented. It is significantly above average in technical depth, especially around mpv IPC details, platform assumptions, and de-risking strategy.

If executed as written, it should produce a working v1. However, there are several gaps that could create avoidable failures in production on Pi hardware and in long-running reliability.

Overall score: **8.2 / 10**

- Architecture clarity: **9/10**
- Risk awareness: **8.5/10**
- Test strategy: **8/10**
- Operational readiness: **7/10**
- Maintainability over time: **7.5/10**

## What Is Excellent

- Good technology fit: Python + mpv + yt-dlp is pragmatic for Pi and local macOS development.
- Correct de-risking choice: tackling player lifecycle early is exactly right.
- Solid event model details: waiting for `file-loaded` before random seek avoids common timing bugs.
- Lean dependency footprint: simple stack, low maintenance burden.
- Explicit concern for SD wear and headless constraints: strong operational thinking.
- TDD phased approach is clear and implementable.

## High-Priority Gaps (Should Fix Before Build Starts)

### 1) Concurrency model is under-specified

The plan uses Flask threaded server + background refresh thread + player IPC control, but does not define shared-state ownership/locking.

**Risk:** race conditions (double next, pause during load, stale status reads).

**Recommendation:** introduce a single `PlaybackController` that serializes all player commands via one worker thread + queue. API handlers should submit commands, not call player directly.

---

### 2) Watchdog mitigation is listed but not planned

Risks section mentions watchdog for mpv unresponsiveness, but no implementation phase includes it.

**Risk:** app hangs forever despite process being alive.

**Recommendation:** add explicit task in player phase:
- heartbeat (`get_property pause`) every N seconds,
- timeout threshold,
- restart and recover current loop state.

---

### 3) "Pinned dependencies" conflicts with `>=` spec

Plan says `requirements.txt` is pinned lock file, but examples use unconstrained minimum versions.

**Risk:** non-reproducible behavior across time and machines.

**Recommendation:** choose one model:
- either true pinning (`==`) with periodic updates, or
- use `pyproject.toml` constraints and generate lock with `pip-tools`.

---

### 4) Boot/offline behavior does not guarantee autoplay

Plan says fetch playlist with retries, then periodic retries, but no fallback source for first boot when network is absent.

**Risk:** violates "autoplay on boot" expectation if network is down. (note from author: if network is down just go into standby and poll until it is back up)

**Recommendation:** define offline policy now:
- persist last successful playlist snapshot to disk, and
- if fresh fetch fails, start from cached snapshot.

---

### 5) Systemd service details are incomplete for robustness

Good start with `TTYPath=/dev/tty1`, but missing important runtime controls.

**Risk:** harder diagnosis and weaker service behavior.

**Recommendation:** include:
- `WorkingDirectory=...`,
- `Environment=PYTHONUNBUFFERED=1`,
- `ExecStartPre=` checks (optional),
- `Restart=always` + sane `StartLimit*`,
- `StandardOutput=journal`,
- clear user/group strategy.

## Medium-Priority Gaps

### 6) macOS parity is not fully codified

Plan mentions cross-platform, but not explicit capability matrix.

**Recommendation:** add a backend capability table in docs:
- Pi: DRM output + display power control.
- macOS: default video output + no-op display power.

---

### 7) Config schema/versioning is absent

Config validation is planned, but no schema versioning.

**Risk:** future breaking changes become painful.

**Recommendation:** include `config_version` and migration/defaulting behavior.

---

### 8) Log API may become expensive/noisy

`GET /api/logs` ring buffer is mentioned, but no retention/size policy.

**Recommendation:** define memory bound (e.g., last 500 entries), server-side filters (`level`, `since`), and poll cadence limits.

---

### 9) Provisioning assumptions may be too optimistic

Claim that some packages "already ship" can drift by image version.

**Recommendation:** make provisioning explicitly idempotent and explicit:
- always `apt install` required packages,
- verify binaries,
- fail with actionable error messages.

---

### 10) Test suite misses contract + soak testing

Strong unit/integration baseline, but long-run behavior is core to this app.

**Recommendation:** add:
- API contract tests (status payload shape),
- 1-2 hour soak test with simulated `next/pause/resume`,
- crash/restart recovery test for stale socket cleanup.

## Suggested Revised Phase Order

Small reorder to reduce rework risk:

1. Scaffolding + logging  
2. Config + schema/versioning  
3. Player + watchdog + controller queue (concurrency boundary)  
4. Playlist + disk snapshot fallback  
5. API + status contract tests  
6. Web UI  
7. Display power module  
8. App orchestration  
9. Provisioning + systemd + hardware soak tests

Rationale: lock control-plane semantics (controller/state machine) before endpoint/UI integration.

## Missing "Definition of Done" (Add This)

For each major feature, add measurable acceptance checks:

- Autoplay on boot within X seconds after network ready.
- `pause` powers monitor off and `play` restores picture within Y seconds.
- No crash or deadlock during 2-hour soak test.
- `next/previous` works correctly across at least N transitions.
- macOS local run works without Pi-specific commands.
- Structured logs include event type, video id/title, action, outcome.

## Concrete Plan Edits Worth Applying

If you want to keep the existing document and strengthen it quickly, add these items:

- New module: `cinegatto/controller/playback_controller.py`
- New tests:
  - `tests/test_controller.py`
  - `tests/test_watchdog.py`
  - `tests/test_boot_offline_fallback.py`
  - `tests/test_status_contract.py`
- New config keys:
  - `config_version`
  - `playlist_cache_path`
  - `watchdog_timeout_sec`
  - `log_ring_size`
- New provisioning checks:
  - verify `mpv`, `python3`, `yt-dlp` availability,
  - explicit package install list,
  - service restart policy + journald routing.

## Final Assessment

This is a **good plan that is close to execution-ready**.  
Main weakness is not architecture but **operational rigor around concurrency, watchdog recovery, and offline startup behavior**.

Address the high-priority items first, and the probability of a smooth Pi deployment goes up materially.


Added from author: what cache strategy should we choose? videos in the playlist might be >12 hours and huge. Caching is crucial though to lower internet usage. Maybe use a path in the config to specify location and size of local cache.
