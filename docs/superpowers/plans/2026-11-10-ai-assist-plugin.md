# AI Assist Plugin Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fork's native AI Assist with an independently installable Gemini plugin that is responsive, privacy-preserving, honest about host compatibility, and limited to verified functionality.

**Architecture:** Package the plugin as a Python 3.12 Pages capability with separate pure modules for compatibility, snapshot filtering, Gemini transport, response parsing, operation construction, and lifecycle. The first release includes preset analysis, defect description, checked-row review, and user-confirmed apply; camera/Telegram/print monitoring remains excluded unless every release gate in the specification is implemented and tested.

**Tech Stack:** Python 3.12, OrcaSlicer Pages plugin API, standard-library HTTP/threading, unittest, local fake HTTP servers, Gemini generateContent API.

**Spec:** `docs/superpowers/specs/2026-11-10-orcaslicer-plugin-host-api-design.md`

**Depends on:** `docs/superpowers/plans/2026-11-10-plugin-host-api.md` for automatic apply. Manual mode must still work on an unpatched official host.

## Global Constraints

- Use feature discovery (`orca.host.api_info()`), never OrcaSlicer version-string comparisons.
- Gemini/API tokens live only in plugin-private storage; never in preset snapshots, logs, URLs shown to users, or source control.
- Requests are cancellable, bounded by connect/total timeouts, and never run on the GUI thread.
- Model output is untrusted: one strict schema, no unknown operation types, and host-side typed validation remains authoritative.
- Only rows explicitly checked by the user are sent to the host transaction.
- Official unpatched hosts load in manual mode; no guessed files, UI automation, or unsupported host calls.
- No monitoring, Telegram, webcam, or Moonraker controls or documentation claims ship unless Task 7's complete gate is chosen and passes.
- Remove native C++ AI wiring only after plugin parity and fallback verification.

## Review Focus

- A late result from request A must not overwrite request B or a cancelled page; Task 4 tests generation IDs and unload cancellation.
- A model response containing a sensitive or non-allowlisted key must be rejected before review display; Task 3 adds the test.
- Applying two checked rows after the workspace changes must surface `conflict` and refresh, never retry silently; Task 5 adds the test.
- Direct webcam URLs must never receive Moonraker credentials if monitoring is later enabled; Task 7 makes this a mandatory gate test.
- Empty, fenced, partial, oversized, or additional-property model output must produce an actionable parse error and no operations; Task 3 adds these tests.

---

### Task 1: Plugin Package Skeleton and Compatibility Modes

**Files:**
- Create: `plugins/ai_assist/pyproject.toml`
- Create: `plugins/ai_assist/src/orca_ai_assist/__init__.py`
- Create: `plugins/ai_assist/src/orca_ai_assist/plugin.py`
- Create: `plugins/ai_assist/src/orca_ai_assist/compat.py`
- Create: `plugins/ai_assist/src/orca_ai_assist/ui.py`
- Create: `plugins/ai_assist/tests/test_compat.py`
- Create: `plugins/ai_assist/tests/fakes.py`

**Interfaces:**
- Produces: `HostFeatures.discover(host: object) -> HostFeatures` with booleans `storage`, `snapshot`, `transaction`, `select`, `values`.
- Produces: `HostMode` values `automatic`, `manual`, `unavailable`.
- Produces: one `orca.pages.PagesPluginCapabilityBase` named `AI Assist` without importing optional monitoring modules.

- [ ] **Step 1: Write failing feature-discovery tests**

Test a full v1 host, unpatched read-only host, host whose `api_info()` raises, newer host retaining required feature strings, and host advertising only a subset. Assert automatic mode requires `preset_snapshot.v1`, `preset_transaction.v1`, and `preset_values.v1`; read-only host is manual, not load failure.

- [ ] **Step 2: Run tests and verify failure**

Run: `python3.12 -m unittest discover -s plugins/ai_assist/tests -v`

Expected: FAIL because package/modules do not exist.

- [ ] **Step 3: Implement package metadata and compatibility layer**

Use a normal wheel layout and package entry file compatible with Orca's wheel loader. `plugin.py` must import `orca` only at registration boundary so pure tests run outside Orca. No `request_permissions` shim or call.

- [ ] **Step 4: Add minimal Pages UI modes**

Render an automatic/manual compatibility banner and only the four finished views: Analyze presets, Describe a defect, Review changes, Apply. Include no monitoring controls.

- [ ] **Step 5: Run tests and package build**

Run: `python3.12 -m unittest discover -s plugins/ai_assist/tests -v && python3.12 -m build plugins/ai_assist`

Expected: PASS and one wheel/sdist.

- [ ] **Step 6: Commit**

```bash
git add plugins/ai_assist
git commit -m "Create AI Assist plugin package

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 2: Private Configuration and Consent

**Files:**
- Create: `plugins/ai_assist/src/orca_ai_assist/config.py`
- Create: `plugins/ai_assist/tests/test_config.py`
- Modify: `plugins/ai_assist/src/orca_ai_assist/plugin.py`
- Modify: `plugins/ai_assist/src/orca_ai_assist/ui.py`

**Interfaces:**
- Produces: `AiConfig.load(data_dir: Path) -> AiConfig` and `save(data_dir: Path) -> None`.
- Fields: `gemini_api_key`, `gemini_model`, `base_url`, `preset_data_consent`, `camera_consent` (reserved false; hidden in v1 UI).
- Produces: `redact(text: str, secrets: Iterable[str]) -> str`.

- [ ] **Step 1: Write failing config, permission, and redaction tests**

Assert atomic JSON save, `0600` best-effort permissions on POSIX, malformed config recovery without secret echo, plugin-storage path use on patched host, actionable no-persistence state without storage, and redaction of API key plus URL query credentials.

- [ ] **Step 2: Run tests and verify failure**

Expected: FAIL because config module is absent.

- [ ] **Step 3: Implement minimal private config**

Store `config.json` under `host.v1.plugin_storage()` only. Use temporary-file plus `os.replace`. Do not migrate or read native `OrcaSlicer.conf`; ask the user to enter the key once in the plugin.

- [ ] **Step 4: Implement first-use consent UI**

Before the first preset request, show categories sent to Gemini and require explicit opt-in. Keep API key masked and never send it to JavaScript after initial save.

- [ ] **Step 5: Run tests**

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/ai_assist/src/orca_ai_assist/config.py \
  plugins/ai_assist/src/orca_ai_assist/plugin.py plugins/ai_assist/src/orca_ai_assist/ui.py \
  plugins/ai_assist/tests/test_config.py
git commit -m "Store AI Assist settings privately

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 3: Snapshot Allowlist and Strict Gemini Schema

**Files:**
- Create: `plugins/ai_assist/src/orca_ai_assist/presets.py`
- Create: `plugins/ai_assist/src/orca_ai_assist/schema.py`
- Create: `plugins/ai_assist/tests/test_presets.py`
- Create: `plugins/ai_assist/tests/test_schema.py`

**Interfaces:**
- Produces: `SNAPSHOT_KEYS: dict[str, tuple[str, ...]]` for print, filament, and printer categories.
- Produces: `build_provider_payload(snapshot: Mapping, problem: str | None) -> dict`.
- Produces: `parse_recommendations(text: str, snapshot: Mapping) -> AnalysisResult`.
- Recommendation fields are exactly `preset_type`, `key`, `proposed_value`, `reason`, and optional `confidence`; trusted `old_value` is joined from snapshot data.

- [ ] **Step 1: Write allowlist and secret-exclusion tests**

Port only useful non-secret keys from native `PresetAnalyzer.cpp`. Assert `printhost_apikey`, token/password patterns, cloud IDs, arbitrary paths, and unrequested values cannot enter the provider payload even if present in the fake snapshot.

- [ ] **Step 2: Write strict parser tests**

Cover valid response, Markdown fences, invalid JSON, absent fields, wrong types, unknown top-level/additional fields, unknown preset category, non-allowlisted key, duplicate key, oversized response, and model-supplied `old_value`. Assert invalid responses yield zero recommendations.

- [ ] **Step 3: Run tests and verify failure**

Expected: FAIL because pure modules are absent.

- [ ] **Step 4: Implement payload construction and schema parser**

Use one documented JSON object with `summary` and `recommendations`. Strip one optional JSON fence, cap response length, validate all fields manually or with a dependency already packaged, and derive old values only from the captured snapshot/revision.

- [ ] **Step 5: Run tests**

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/ai_assist/src/orca_ai_assist/presets.py \
  plugins/ai_assist/src/orca_ai_assist/schema.py \
  plugins/ai_assist/tests/test_presets.py plugins/ai_assist/tests/test_schema.py
git commit -m "Validate AI preset recommendations strictly

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 4: Cancellable Gemini Transport

**Files:**
- Create: `plugins/ai_assist/src/orca_ai_assist/gemini.py`
- Create: `plugins/ai_assist/src/orca_ai_assist/tasks.py`
- Create: `plugins/ai_assist/tests/test_gemini.py`
- Create: `plugins/ai_assist/tests/test_tasks.py`
- Modify: `plugins/ai_assist/src/orca_ai_assist/plugin.py`

**Interfaces:**
- Produces: `GeminiClient.generate(payload: Mapping, cancel: threading.Event) -> GeminiResponse`.
- Produces: `TaskController.submit(kind: str, work: Callable, callback: Callable) -> int`, `cancel_current()`, and `shutdown(timeout: float) -> bool`.
- Callbacks carry a generation ID and are ignored unless still current and plugin is loaded.

- [ ] **Step 1: Write local fake HTTP transport tests**

Assert method/body/content type, bounded timeout, HTTP/non-JSON/body-size errors, key redaction, cancellation before send, and cancellation after response before callback. The fake server must require no live Gemini service.

- [ ] **Step 2: Write lifecycle race tests**

Start request A, supersede with B, complete A last, and assert only B updates the UI. Start a blocked request, unload, and assert shutdown returns within its bound and no later callback is posted.

- [ ] **Step 3: Run tests and verify failure**

Expected: FAIL because transport/task modules are absent.

- [ ] **Step 4: Implement transport and task controller**

Use HTTPS by default, reject non-HTTPS base URLs except explicit localhost test endpoints, keep the API key out of exception strings, use bounded body reads, and communicate cancellation cooperatively. If the API requires a query key, redact the final URL before every diagnostic.

- [ ] **Step 5: Wire page messages without blocking `on_message`**

Snapshot capture remains a short GUI call; provider request and parse run in a worker; `post_message` occurs only for the current generation.

- [ ] **Step 6: Run tests**

Expected: PASS.

- [ ] **Step 7: Request Python and security reviews**

Use `python-reviewer` and `security-reviewer` on the plugin package. Fix confirmed transport, secret-handling, and lifecycle findings; rerun tests.

- [ ] **Step 8: Commit**

```bash
git add plugins/ai_assist/src/orca_ai_assist/gemini.py \
  plugins/ai_assist/src/orca_ai_assist/tasks.py plugins/ai_assist/src/orca_ai_assist/plugin.py \
  plugins/ai_assist/tests/test_gemini.py plugins/ai_assist/tests/test_tasks.py
git commit -m "Run Gemini requests asynchronously

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 5: Checked-Row Apply and Manual Fallback

**Files:**
- Create: `plugins/ai_assist/src/orca_ai_assist/apply.py`
- Create: `plugins/ai_assist/tests/test_apply.py`
- Modify: `plugins/ai_assist/src/orca_ai_assist/plugin.py`
- Modify: `plugins/ai_assist/src/orca_ai_assist/ui.py`

**Interfaces:**
- Produces: `build_commit_request(revision: str, recommendations: Sequence, selected_ids: Collection[str]) -> dict`.
- Produces: `apply_recommendations(host, analysis, selected_ids) -> ApplyResult`.
- Consumes host operation-list API from the host plan: `host.v1.presets.commit(request)`.

- [ ] **Step 1: Write selected-row and trust-boundary tests**

Given three recommendations with one checked row, assert exactly one `set_values` operation is emitted. Assert unknown IDs, modified JavaScript values, empty selection, and duplicate IDs cannot alter the reviewed Python-side recommendation objects.

- [ ] **Step 2: Write host outcome tests**

Cover `applied`, `cancelled`, `conflict`, `invalid`, `unsupported`, and `error`. On conflict, assert the UI requests a fresh snapshot and discards old review rows; it must not silently retry.

- [ ] **Step 3: Write manual-mode tests**

With no transaction feature, assert Apply becomes “Copy instructions”, provides category/key/current/proposed values, and never calls absent host methods. Plugin registration and analysis remain available using existing read-only bindings where safe.

- [ ] **Step 4: Run tests and verify failure**

Expected: FAIL because apply module is absent.

- [ ] **Step 5: Implement immutable review IDs and commit building**

Generate row IDs in Python, retain canonical records outside page-controlled data, and send only `interaction="require_user_confirmation"` plus captured revision.

- [ ] **Step 6: Run tests and package smoke test**

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add plugins/ai_assist/src/orca_ai_assist/apply.py \
  plugins/ai_assist/src/orca_ai_assist/plugin.py plugins/ai_assist/src/orca_ai_assist/ui.py \
  plugins/ai_assist/tests/test_apply.py
git commit -m "Apply only reviewed AI preset changes

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 6: Plugin Parity, Native Removal, and Documentation

**Files:**
- Create: `plugins/ai_assist/README.md`
- Create: `plugins/ai_assist/tests/test_plugin_lifecycle.py`
- Modify: `docs/AI_ASSIST.md`
- Modify: `src/slic3r/CMakeLists.txt:23-35`
- Modify: `src/slic3r/GUI/MainFrame.cpp` (AI Assist include/menu entry)
- Modify: `src/slic3r/GUI/Preferences.cpp` (native AI settings)
- Modify: `src/slic3r/GUI/Preferences.hpp` (native AI controls)
- Modify: `src/slic3r/GUI/GUI_App.cpp` (PrintMonitor startup/shutdown)
- Delete: `src/slic3r/GUI/AIAssist/AIAssistDialog.cpp`
- Delete: `src/slic3r/GUI/AIAssist/AIAssistDialog.hpp`
- Delete: `src/slic3r/GUI/AIAssist/AIConfig.hpp`
- Delete: `src/slic3r/GUI/AIAssist/GeminiClient.cpp`
- Delete: `src/slic3r/GUI/AIAssist/GeminiClient.hpp`
- Delete: `src/slic3r/GUI/AIAssist/PresetAnalyzer.cpp`
- Delete: `src/slic3r/GUI/AIAssist/PresetAnalyzer.hpp`
- Delete: `src/slic3r/GUI/AIAssist/PrintMonitor.cpp`
- Delete: `src/slic3r/GUI/AIAssist/PrintMonitor.hpp`
- Delete: `src/slic3r/GUI/AIAssist/TelegramClient.cpp`
- Delete: `src/slic3r/GUI/AIAssist/TelegramClient.hpp`
- Delete: `src/slic3r/GUI/AIAssist/WebcamCapture.cpp`
- Delete: `src/slic3r/GUI/AIAssist/WebcamCapture.hpp`

**Interfaces:**
- Consumes completed plugin package.
- Produces no native/provider-specific AI code in OrcaSlicer core.

- [ ] **Step 1: Write lifecycle/load smoke test**

With fake `orca` modules, register capability, load, analyze, cancel, unload twice, and assert no worker, listener, host reference, or callback remains. Verify manual and automatic host fixtures.

- [ ] **Step 2: Run plugin suite and verify parity gates**

Required gates: analyze preset, describe defect, render checked review rows, automatic confirmed apply, manual instructions, cancellation, secret exclusion, plugin install/load/unload.

- [ ] **Step 3: Remove native wiring and C++ sources**

Delete native AI files and all includes/menu/preferences/startup/CMake references only after Step 2 passes. Do not retain dormant monitor controls.

- [ ] **Step 4: Rewrite documentation to match shipped behavior**

State Gemini-only preset assistance, privacy payload, automatic/manual host modes, installation/update, no monitoring/Telegram/webcam in this release, and independent plugin versioning.

- [ ] **Step 5: Build and test the host plus plugin**

Run:

```bash
python3.12 -m unittest discover -s plugins/ai_assist/tests -v
python3.12 -m build plugins/ai_assist
cmake --build build --config RelWithDebInfo --target all
ctest --test-dir build -C RelWithDebInfo --output-on-failure
git diff --check
```

Expected: all available checks PASS; no native AIAssist references remain (`git grep AIAssist -- src` returns none).

- [ ] **Step 6: Request required reviews**

Use `python-reviewer` for the package, `cpp-reviewer` for C++ removal/wiring changes, and `security-reviewer` for provider/privacy boundaries. Fix confirmed findings and rerun Step 5.

- [ ] **Step 7: Commit**

```bash
git add -A plugins/ai_assist docs/AI_ASSIST.md src/slic3r
git commit -m "Replace native AI Assist with plugin

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 7: Optional Monitoring Release Gate — Exclude by Default

**Files:**
- Create only if all gates are approved for this release: `plugins/ai_assist/src/orca_ai_assist/monitoring.py`
- Create only if all gates are approved: `plugins/ai_assist/src/orca_ai_assist/moonraker.py`
- Create only if all gates are approved: `plugins/ai_assist/src/orca_ai_assist/telegram.py`
- Create only if all gates are approved: `plugins/ai_assist/tests/test_monitoring.py`
- Create only if all gates are approved: `plugins/ai_assist/tests/test_moonraker.py`
- Create only if all gates are approved: `plugins/ai_assist/tests/test_telegram.py`

**Interfaces:**
- Optional; no later task depends on these files.

- [ ] **Step 1: Keep monitoring excluded unless every gate is scheduled**

Default action: make no files and ensure README/UI make no monitoring claim. This satisfies the specification's first-release rule.

- [ ] **Step 2: If explicitly included, write all gate tests before implementation**

Tests must cover identical prompt/parser anomaly schema, active-print query, minimum interval/cost controls, exact credential origin matching, direct-camera no-auth, Telegram chat and sender authorization, explicit stop confirmation/configuration, bounded shutdown, cancellation, and local HTTP fakes.

- [ ] **Step 3: Implement only after the full test set fails for expected reasons**

Do not copy the native synchronous code. Reuse Task 4 cancellation/lifecycle infrastructure and keep stop authorization fail-closed.

- [ ] **Step 4: Require Python and security review, then rerun the entire plugin suite**

Any unclosed gate removes the feature files/UI/docs from the release rather than shipping them as experimental.
