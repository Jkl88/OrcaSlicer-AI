# Filament QR Plugin Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-file `orca_filament` prototype into a tested, durable, secure-enough trusted-LAN plugin that selects filament slots through the generic host API and degrades honestly on official unpatched OrcSlicer.

**Architecture:** Split the plugin into a wheel package with pure inventory, QR, authentication, HTTP, host adapter, and Pages UI modules. SQLite owns spool, schema, and pending-scan durability; the GUI thread owns short host snapshot/transaction calls; the LAN server only enqueues work and never calls Orca host APIs directly.

**Tech Stack:** Python 3.12, sqlite3, `qrcode`, Pillow, zxing-cpp, `http.server`, unittest, OrcaSlicer Pages plugin API.

**Spec:** `docs/superpowers/specs/2026-11-10-orcaslicer-plugin-host-api-design.md`

**Depends on:** `docs/superpowers/plans/2026-11-10-plugin-host-api.md` for automatic slot selection. Manual fallback remains supported without the patch.

## Global Constraints

- Preserve QR payload `FQ:FQ-XXXXXX` and printable SVG size 56 × 72 mm.
- Plugin state lives only in `orca.host.v1.plugin_storage()`; no guessed Orca configuration paths.
- Pending scans are committed to SQLite before any host mutation and survive restart.
- HTTP is explicitly trusted-LAN only; never claim TLS or Internet-safe deployment.
- All request bodies, image dimensions, decode work, concurrent requests, and authentication attempts are bounded.
- Sessions expire, support revocation, and are cleared when PIN/security settings change.
- HTTP threads never call `orca.host`; Pages `on_message` performs host calls on the GUI thread.
- No unsupported `orca.request_permissions()` call is retained.
- Automatic selection uses fresh revision plus `interaction="require_user_confirmation"`; cancellation/conflict leaves the pending record retryable.

## Review Focus

- A process crash after scan receipt but before selection must leave a retryable pending record; Task 2 adds restart tests.
- Concurrent scans for different slots must not overwrite each other or complete the wrong request; Task 2 tests queue ordering and IDs.
- An image with small compressed bytes but enormous dimensions must be rejected before expensive conversion; Task 4 adds decompression-bomb tests.
- A session must be invalid immediately after PIN rotation/revocation and after expiry, including already-open keep-alive requests; Task 3 adds tests.
- A host `applied` result for a different slot/profile/revision must not complete the pending scan; Task 5 verifies result correlation.

---

### Task 1: Package Split and Loadable Compatibility Layer

**Files:**
- Create: `plugins/orca_filament/pyproject.toml`
- Create: `plugins/orca_filament/src/orca_filament/__init__.py`
- Create: `plugins/orca_filament/src/orca_filament/plugin.py`
- Create: `plugins/orca_filament/src/orca_filament/compat.py`
- Create: `plugins/orca_filament/tests/fakes.py`
- Create: `plugins/orca_filament/tests/test_compat.py`
- Move/refactor source from: `orca_filament/orca_filament_qr.py`

**Interfaces:**
- Produces: `HostCapabilities.discover(host) -> HostCapabilities`.
- Produces modes `automatic`, `manual`, and `storage_unavailable`.
- Produces one Pages capability `Filament QR` that loads without calling nonexistent APIs.

- [ ] **Step 1: Write failing load and discovery tests**

Test patched host, stock read-only host, missing `api_info`, partial features, and storage absence. Assert capability registration never calls `orca.request_permissions`; stock host enters manual mode rather than failing registration.

- [ ] **Step 2: Run tests and verify failure**

Run: `python3.12 -m unittest discover -s plugins/orca_filament/tests -v`

Expected: FAIL because package does not exist.

- [ ] **Step 3: Create wheel package and compatibility adapter**

Move registration behind import-safe boundaries. Keep dependencies constrained as prototype: `qrcode>=7.4,<9`, `Pillow>=10,<13`, `zxing-cpp>=2.3,<4`.

- [ ] **Step 4: Remove unsupported registration calls**

Replace `orca.host.plugin.storage()` with discovered `orca.host.v1.plugin_storage()`. If unavailable, disable persistence with an actionable UI state; do not silently use home/current directories in plugin mode. Standalone tests may accept an explicit `--data` path.

- [ ] **Step 5: Run tests and build package**

Run: `python3.12 -m unittest discover -s plugins/orca_filament/tests -v && python3.12 -m build plugins/orca_filament`

Expected: PASS and wheel/sdist output.

- [ ] **Step 6: Commit**

```bash
git add plugins/orca_filament orca_filament/orca_filament_qr.py
git commit -m "Package Filament QR plugin

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 2: Versioned SQLite Store and Durable Pending Queue

**Files:**
- Create: `plugins/orca_filament/src/orca_filament/store.py`
- Create: `plugins/orca_filament/src/orca_filament/models.py`
- Create: `plugins/orca_filament/tests/test_store.py`
- Modify: `plugins/orca_filament/src/orca_filament/plugin.py`

**Interfaces:**
- Produces: `Store.open(path: Path) -> Store`, `migrate()`, `add_spool(...)`, `get_spool(code)`, `delete_spool(code)`.
- Produces: `enqueue_scan(code: str, slot: int) -> PendingScan`, `list_pending()`, `mark_attempt(id, status, diagnostics)`, `mark_complete(id, host_revision)`.
- Schema uses `PRAGMA user_version`; v1 has `spools`, `pending_scans`, and required indexes.

- [ ] **Step 1: Write failing schema creation/migration tests**

Create empty DB and legacy prototype DB, open through `Store`, and assert versioned schema plus preservation of all spool rows. Assert migration transaction rollback leaves the prior DB usable after injected failure.

- [ ] **Step 2: Write collision and concurrency tests**

Patch token generation to collide 19 times then succeed; assert retry. Run concurrent adds/scans and assert unique codes, no locked-error leak after configured retry, and deterministic pending IDs/order.

- [ ] **Step 3: Write durable pending/restart tests**

Enqueue before host call, close/reopen store, and assert status/profile/printer/slot/attempt diagnostics survive. Assert two scans do not overwrite each other and completion addresses an ID, not global `pending` memory.

- [ ] **Step 4: Run tests and verify failure**

Expected: FAIL because store module is absent.

- [ ] **Step 5: Implement explicit transactions and migrations**

Use parameterized SQL, WAL only if cross-platform tests pass, bounded busy timeout, and transaction contexts. Preserve prototype spool fields; add `slot`, status timestamps, attempts, and host revision to pending records.

- [ ] **Step 6: Run tests**

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add plugins/orca_filament/src/orca_filament/store.py \
  plugins/orca_filament/src/orca_filament/models.py \
  plugins/orca_filament/src/orca_filament/plugin.py \
  plugins/orca_filament/tests/test_store.py
git commit -m "Persist filament scan queue

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 3: Expiring Sessions, Revocation, and PIN Throttling

**Files:**
- Create: `plugins/orca_filament/src/orca_filament/auth.py`
- Create: `plugins/orca_filament/tests/test_auth.py`
- Modify: `plugins/orca_filament/src/orca_filament/plugin.py`

**Interfaces:**
- Produces: `SessionManager.authenticate(pin, client_ip, now) -> token`, `validate(token, now)`, `revoke(token)`, `revoke_all()`, `rotate_pin()`.
- Session records include creation, last-seen, and absolute expiry; tokens are held as one-way digests in memory.

- [ ] **Step 1: Write failing session tests with fake clock**

Cover success, wrong PIN, six failures in 60 seconds, recovery after window, idle expiry, absolute expiry, explicit revoke, revoke-all, PIN rotation, constant-time comparison seam, and independent IP buckets.

- [ ] **Step 2: Run tests and verify failure**

Expected: FAIL because auth module is absent.

- [ ] **Step 3: Implement bounded authentication state**

Cap tracked IP entries and prune old attempts/sessions. Generate cryptographic six-digit PIN and 256-bit URL-safe session token. Cookie remains `HttpOnly; SameSite=Strict; Path=/`; add `Max-Age` matching session policy.

- [ ] **Step 4: Run tests**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugins/orca_filament/src/orca_filament/auth.py \
  plugins/orca_filament/src/orca_filament/plugin.py \
  plugins/orca_filament/tests/test_auth.py
git commit -m "Expire and revoke Filament QR sessions

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 4: QR/Label Core and Bounded Decode

**Files:**
- Create: `plugins/orca_filament/src/orca_filament/qr.py`
- Create: `plugins/orca_filament/tests/test_qr.py`
- Modify: `plugins/orca_filament/src/orca_filament/plugin.py`

**Interfaces:**
- Produces: `normalize_code(text: str) -> str`, `qr_svg(spool: Spool) -> str`, `decode_qr_image(data: bytes) -> str`.
- Exact limits: request bytes `6 MiB`; decoded dimensions no larger than 2600 × 2600 and total pixels no larger than 6,760,000; one supported FQ code required.

- [ ] **Step 1: Write normalization and SVG tests**

Assert payload remains `FQ:FQ-XXXXXX`, SVG is 56 mm × 72 mm, XML escaping protects brand/profile/material, invalid codes reject, and generated SVG contains no remote resource/script.

- [ ] **Step 2: Write optional QR round-trip test**

When qrcode/Pillow/zxing-cpp are installed, render/decode a generated QR and assert the normalized code. Skip with an explicit reason if an optional decoder is absent.

- [ ] **Step 3: Write malformed/oversized/decompression tests**

Cover body over 6 MiB, invalid format, truncated image, width/height/pixel overflow, Pillow decompression warning promoted to rejection, multiple unrelated codes, and decoder exception. Assert no scan is enqueued on failure.

- [ ] **Step 4: Run tests and verify failure**

Expected: FAIL because QR module is absent.

- [ ] **Step 5: Implement safe image inspection before decode**

Open with Pillow under decompression-warning rejection, validate dimensions before thumbnail/materialization, normalize EXIF only after limits, and pass a bounded image to zxing-cpp.

- [ ] **Step 6: Run tests**

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add plugins/orca_filament/src/orca_filament/qr.py \
  plugins/orca_filament/src/orca_filament/plugin.py \
  plugins/orca_filament/tests/test_qr.py
git commit -m "Harden filament QR labels and decoding

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 5: Host Snapshot and Slot Selection Workflow

**Files:**
- Create: `plugins/orca_filament/src/orca_filament/host.py`
- Create: `plugins/orca_filament/tests/test_host.py`
- Modify: `plugins/orca_filament/src/orca_filament/plugin.py`
- Modify: `plugins/orca_filament/src/orca_filament/ui.py` (create if not introduced in Task 1)

**Interfaces:**
- Produces: `read_workspace(host) -> WorkspaceState`.
- Produces: `attempt_pending(host, store, pending_id) -> SelectionOutcome`.
- Automatic request contains one operation: `{"op":"select","preset_type":"filament","name":profile,"scope":{"slot":slot}}` with fresh expected revision.

- [ ] **Step 1: Write automatic-mode outcome tests**

Cover already active, applied, cancelled, conflict, invalid, unsupported, error, wrong printer, missing profile, incompatible profile, and slot outside snapshot. Assert pending is marked complete only for correlated `applied`/already-active outcome.

- [ ] **Step 2: Write result-correlation and restart tests**

Return an `applied` result whose affected profile/slot does not match and assert pending remains failed/retryable. Reopen store with pending item and successfully retry against a fresh revision.

- [ ] **Step 3: Write manual fallback tests**

Without selection/transaction features, assert scan remains `manual_switch`, UI shows exact profile and zero-based slot rendered as user-facing slot `N+1`, and no absent API is called.

- [ ] **Step 4: Run tests and verify failure**

Expected: FAIL because host adapter is absent.

- [ ] **Step 5: Implement GUI-thread host adapter**

LAN handler only enqueues and signals. Pages polling/message handler reads pending rows, captures a fresh snapshot, commits one transaction, and records outcome. Never reuse a snapshot revision after conflict.

- [ ] **Step 6: Run tests**

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add plugins/orca_filament/src/orca_filament/host.py \
  plugins/orca_filament/src/orca_filament/plugin.py \
  plugins/orca_filament/src/orca_filament/ui.py \
  plugins/orca_filament/tests/test_host.py
git commit -m "Select filament slots through host transactions

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 6: Bounded Trusted-LAN HTTP Service

**Files:**
- Create: `plugins/orca_filament/src/orca_filament/server.py`
- Create: `plugins/orca_filament/tests/test_server.py`
- Modify: `plugins/orca_filament/src/orca_filament/plugin.py`

**Interfaces:**
- Produces: `FilamentHttpServer.start() -> ServerAddress`, `stop(timeout: float) -> bool`.
- Endpoints: `POST /api/auth`, `POST /api/logout`, `GET /api/status`, `POST /api/scan`, `POST /api/decode`, `GET /label/<code>`.
- Mutating endpoints require an authenticated session; server calls Store/QR services, never Orca host.

- [ ] **Step 1: Write local HTTP endpoint tests**

Use loopback and ephemeral port. Cover content type, method, auth, malformed JSON, missing/negative/non-numeric/oversized Content-Length, slow/truncated body, unknown route, path decoding, label escaping, security headers, and cookie properties.

- [ ] **Step 2: Write concurrency/rate-limit tests**

Saturate bounded worker/decode semaphores and assert excess requests get 429/503 promptly rather than unbounded threads. Assert auth and decode rate limits are independent and memory-bounded.

- [ ] **Step 3: Write bind conflict and shutdown tests**

Occupy the port and assert actionable error. Start in-flight requests, call stop, assert bounded return, listener closure, worker cleanup, and second stop safety.

- [ ] **Step 4: Run tests and verify failure**

Expected: FAIL because server module is absent.

- [ ] **Step 5: Implement bounded server**

Subclass only enough to cap active handlers; set socket/request timeouts; never reflect raw exception details to clients. Keep `0.0.0.0` opt-in with a prominent trusted-LAN warning; default first-run bind may be loopback until the user enables LAN.

- [ ] **Step 6: Run tests**

Expected: PASS.

- [ ] **Step 7: Request Python and security reviews**

Use `python-reviewer` and `security-reviewer`; fix confirmed input, authentication, concurrency, SQLite, and shutdown issues; rerun all plugin tests.

- [ ] **Step 8: Commit**

```bash
git add plugins/orca_filament/src/orca_filament/server.py \
  plugins/orca_filament/src/orca_filament/plugin.py \
  plugins/orca_filament/tests/test_server.py
git commit -m "Bound Filament QR LAN service

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 7: Finished Pages UI and Lifecycle

**Files:**
- Create: `plugins/orca_filament/src/orca_filament/ui.py` (if not already created)
- Create: `plugins/orca_filament/tests/test_lifecycle.py`
- Modify: `plugins/orca_filament/src/orca_filament/plugin.py`
- Modify: `plugins/orca_filament/README.md`
- Modify: `orca_filament/README.md` (replace prototype claims or point to packaged plugin)

**Interfaces:**
- Consumes Store, auth, QR, server, and host adapter.
- Produces one clean unload path and accurate user documentation.

- [ ] **Step 1: Write lifecycle tests**

Load/start/tick/unload twice. Assert DB closes, listener closes, sessions revoke, no thread remains, no post occurs after unload, and pending scans remain in DB for next load.

- [ ] **Step 2: Implement UI states**

Show storage/API mode, bind address, trusted-LAN warning, PIN and rotate/revoke controls, printer/slots, spool creation, durable queue with retry/manual action, exact transaction outcome, labels, and server errors. Remove any control whose backend is not implemented.

- [ ] **Step 3: Rewrite documentation**

Document installation, dependencies, automatic versus manual selection, zero-based internal/user-facing slots, SQLite backup as a manual file operation only (do not advertise backup/restore UI), HTTP limitations, firewall guidance, session expiry, and exact tests.

- [ ] **Step 4: Run complete package verification**

Run:

```bash
python3.12 -m unittest discover -s plugins/orca_filament/tests -v
python3.12 -m build plugins/orca_filament
git diff --check
```

Expected: PASS; plugin source has no `request_permissions` or `host.plugin.storage` references.

- [ ] **Step 5: Perform local integration smoke test**

Start standalone on loopback/ephemeral port, authenticate, add fixture spool, scan/decode, restart, verify pending row, and stop. Do not bind publicly during automated verification.

- [ ] **Step 6: Request final Python/security reviews and fix confirmed findings**

Rerun Step 4 after fixes.

- [ ] **Step 7: Commit**

```bash
git add plugins/orca_filament orca_filament/README.md
git commit -m "Finish Filament QR plugin

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```
