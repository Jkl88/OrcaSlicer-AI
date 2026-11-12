# Plugins on Official OrcaSlicer v2.5.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship AI Assist and Filament QR as independently versioned plugins that run on the unmodified official OrcaSlicer v2.5.0 through its built-in plugin API, with a verified compatibility manifest and CI — no patched host, no fork installer, no upstream PR.

**Architecture:** A new branch `plugins-v2.5.0` starts at the official v2.5.0 commit and carries only plugin files, plugin tests, release tooling, CI and docs. Each plugin keeps its single-file `.py` package and swaps the fork-only `orca.host.v1` calls for upstream `orca.host.preset.*`, `orca.host.plugin.storage()`, `orca.host.ui.message()` and `orca.request_permissions()`. Preset mutations run on a plugin-owned worker thread because upstream forbids `begin_update()` on the UI thread.

**Tech Stack:** Python 3.12 (plugin runtime; tests also run on 3.10), `unittest`, JSON, GitHub Actions, git worktrees.

**Spec:** `docs/superpowers/specs/2026-11-10-orcaslicer-plugin-host-api-design.md` plus the "Revision 2026-11-13" section that Task 1 adds to it. This plan supersedes `docs/superpowers/plans/2026-11-10-plugin-distribution-upstream.md`.

## Decisions taken with the user (2026-11-13)

- Adopt the upstream API; drop the fork's C++ host API (`orca.host.api_info`, `orca.host.v1.*`). Its code stays only in the history of branch `plugins-release`.
- AI Assist applies one preset category per action (process, filament or printer). Each action is one upstream transaction and is atomic. There is no cross-category atomicity.
- Release only plugin packages and the compatibility manifest. No patched OrcaSlicer build, no fork Windows installer.
- No upstream pull request.

## Upstream v2.5.0 facts this plan relies on (verified in `official-v2.5.0`)

- Base: tag `v2.5.0` = `40ecc66f78ed72dc368e0c1ebe40d77f00cc8a19`. It is fetched locally as tag `official-v2.5.0`.
- Package formats accepted by the Plugins dialog: one `.py` (PEP 723 header with `[tool.orcaslicer.plugin]`) or one `.whl`.
- `orca.host.preset.process|printer.begin_update(preset_name="")` and `orca.host.preset.filament.begin_update(slot_number, preset_name="")` return a `PresetTransaction` with `set(key, value)`, `commit() -> PresetUpdateResult` and `target_name`. `commit()` applies all staged values or none; it raises `ValueError` for an invalid key or value and `RuntimeError` when the target or slot mapping changed. `begin_update()` raises `RuntimeError` when the user cancels the unsaved-edit prompt and `ValueError` for an unavailable target. Slots are 1-based. For filament, `begin_update(slot, name)` already selects `name` into the slot through the normal UI flow, including its prompts.
- `begin_update()`, `set()` and `commit()` must not be called on the UI thread ("never from the UI thread"). From any other thread they block while the UI thread does the work.
- `orca.host.preset_bundle()` is a read API: `.prints|.filaments|.printers.edited_preset()` returns a `Preset` with `.name`, `.is_dirty`, `.is_visible`, `.is_compatible` and `.config_value(key)`. `current_filament_preset_names()` is a list indexed by zero-based slot. `printers.selected_preset_name()` is also available. These are read on the GUI thread (Pages callbacks).
- `orca.host.plugin.storage() -> str` is the per-plugin dir under `plugin_data`. It must be called from a plugin callback.
- `orca.host.ui.message(text, title="OrcaSlicer", buttons="ok", icon="info") -> str` takes `buttons="yes_no"` and returns `"yes"` or `"no"`. It is marshalled to the UI thread.
- `orca.request_permissions(fs_read=[], fs_readwrite=[], network_http=[], network_socket=[], process=[], threading=False, network=[])` is valid only inside `register_capabilities`. `network` URLs normalize to `scheme://host`. A bare host in `network_socket` covers raw sockets on any port. `threading=True` is required for `threading.Thread`. `data_dir()`, which includes plugin storage, is an implicit read-write root. Off the main thread an ungranted event is denied (`PermissionError`) and the prompt is posted asynchronously.
- The Pages capability Python API (`get_ui`, `get_icon`, `on_message`, `on_load`, `on_unload`, `post_message`) is unchanged from the fork.

## Global Constraints

- Branch `plugins-v2.5.0` is cut from `official-v2.5.0` (`40ecc66f78ed72dc368e0c1ebe40d77f00cc8a19`). No file under `src/`, `deps/`, `resources/` or `localization/` is changed on it.
- Plugins stay single-file `.py` packages: `plugins/ai_assist/orca_ai_assist.py` and `plugins/filament_qr/orca_filament_qr.py`.
- No plugin calls `orca.host.api_info` or `orca.host.v1`. Feature detection uses `callable(getattr(...))` on upstream names only.
- No `begin_update`, `set` or `commit` call runs on the GUI thread. Tests assert this with a fake host that raises when called from the thread that ran `on_message`.
- Every preset mutation is preceded by a native `ui.message(..., buttons="yes_no")` confirmation listing old and new values. `"no"` applies nothing.
- `orca.request_permissions` is called only when `callable(getattr(orca, "request_permissions", None))`. Older hosts have no permission system.
- Fallback for hosts without `orca.host.preset`: AI Assist uses manual copy mode. Filament QR uses manual selection with an instruction. Hosts without `plugin.storage` get the existing no-storage behavior.
- Secrets (Gemini key) never appear in logs, status text, the manifest or the confirmation text. Existing redaction stays.
- The manifest records a platform as `passed` only after a real run. Otherwise it records `not-tested`.
- Plugin versions are bumped to `1.1.0` (behavior change: new host API).

## Review Focus

- Stale analysis: the user edits a preset between AI analysis and apply. The plugin must re-read the current values on the GUI thread, refuse rows whose current value differs from `old_value`, and apply nothing in that category (Task 2 `test_apply_rejects_stale_values`).
- The edited filament preset is not assigned to any slot (the filament tab shows a preset no slot uses). The plugin must refuse the filament category with a clear message instead of editing an arbitrary slot (Task 2 `test_filament_apply_without_assigned_slot_refuses`).
- Unload while a worker is inside `begin_update()`. Unload must not block the GUI thread forever, and a late worker result must not be posted to a dead page (Task 2 `test_unload_during_apply_drops_result`, Task 3 `test_unload_during_select_drops_result`).
- Permission denial off the main thread (`PermissionError` from `socket.connect` or `bind`). The user sees an actionable message naming the Plugins dialog permission, not a traceback (Task 2 `test_gemini_permission_error_message`, Task 3 `test_server_bind_permission_error_message`).
- A QR scan queued twice for the same slot while a selection worker is running. Only one `begin_update` may be in flight per plugin. The second request stays `queued` until the first finishes (Task 3 `test_single_selection_in_flight`).

---

### Task 1: Branch on official v2.5.0 and import plugins

**Files:**
- Create branch/worktree: `plugins-v2.5.0` at `official-v2.5.0`, worktree `.claude/worktrees/plugins-v2.5.0`
- Copy from `plugins-release` (`git checkout plugins-release -- <paths>`): `plugins/ai_assist/`, `plugins/filament_qr/`, `docs/superpowers/specs/2026-11-10-orcaslicer-plugin-host-api-design.md`, `docs/superpowers/plans/2026-11-10-*.md`, `docs/superpowers/plans/2026-11-13-plugins-on-official-v2.5.0.md`
- Modify: the copied spec, by appending section `## 21. Revision 2026-11-13`
- Modify: `.gitignore` only to add `plugins/**/__pycache__/` if `git status` shows caches

**Interfaces:**
- Produces: branch `plugins-v2.5.0` on which both suites run with `python3 -m unittest discover -s plugins/<p>/tests -t plugins/<p>`.

- [ ] **Step 1: Create the worktree** using superpowers:using-git-worktrees: `git worktree add -b plugins-v2.5.0 .claude/worktrees/plugins-v2.5.0 official-v2.5.0`. Expected: `git -C .claude/worktrees/plugins-v2.5.0 rev-parse HEAD` prints `40ecc66f78ed72dc368e0c1ebe40d77f00cc8a19`.
- [ ] **Step 2: Copy the plugin and doc paths** listed above from `plugins-release`. Do not copy `plugins/orca_filament/` (empty leftover), `docs/plugin-host-api-v1.md` or `sandboxes/orca_preset_transaction_plugin_any.py`.
- [ ] **Step 3: Run both suites unchanged.** Expected: AI Assist `Ran 92 tests ... OK` and Filament QR `Ran 61 tests ... OK`. They are pure Python and must not depend on host C++.
- [ ] **Step 4: Append the spec revision.** One section that records the four user decisions above, states that sections 4.1, 9.4–9.6, 16 and 17 are replaced by this plan for the release, and states that "atomic" in section 19 now means "atomic per preset category action".
- [ ] **Step 5: Commit** `Import plugins onto official OrcaSlicer v2.5.0`.

### Task 2: AI Assist on the upstream preset API

**Files:**
- Modify: `plugins/ai_assist/orca_ai_assist.py`: `HostFeatures`, `capture_workspace`, `_resolve_storage`, `_on_apply`, apply helpers, page HTML apply buttons, `AiAssistPlugin.register_capabilities`, header `version`
- Modify: `plugins/ai_assist/tests/test_plugin.py` (fake host), `plugins/ai_assist/tests/test_core.py`
- Modify: `plugins/ai_assist/README.md`, `docs/AI_ASSIST.md` (copy from `plugins-release` in this task)

**Interfaces:**
- Consumes: upstream facts above.
- Produces:
  - `HostFeatures(read: bool, mutate: bool, storage: bool)` with `discover(host) -> HostFeatures`, where `read = callable(getattr(host, "preset_bundle", None))`, `mutate = read and callable(getattr(getattr(getattr(host, "preset", None), "process", None), "begin_update", None))` and `storage = callable(getattr(getattr(host, "plugin", None), "storage", None))`. `mode` is `MODE_AUTOMATIC` if `mutate`, `MODE_MANUAL` if `read`, otherwise `MODE_UNAVAILABLE`.
  - `Workspace` keeps `names` and `values`. The `revision` field is removed, and so is `Analysis.revision`.
  - `stale_rows(workspace_now: Workspace, rows: Sequence[Recommendation]) -> List[Recommendation]`: rows whose current value differs from `old_value`.
  - `filament_slot_for(bundle) -> Optional[int]`: the 1-based first slot whose `current_filament_preset_names()[i]` equals `filaments.edited_preset().name`, otherwise `None`.
  - `apply_category(host, category: str, slot: Optional[int], rows: Sequence[Recommendation], expected_name: str) -> ApplyOutcome`. Runs on a worker. It calls `ui.message` with `buttons="yes_no"`, then `begin_update`, checks `tx.target_name == expected_name` (no commit on mismatch), then `set` for each row and `commit()`.
  - Page message `{"type": "apply", "category": "print"|"filament"|"printer", "selected": [row_id, ...]}`. The page shows one "Apply" button per category that has checked rows.
  - Feature constants `FEATURE_*` and `build_commit_request` / `describe_commit_result` are deleted.

- [ ] **Step 1: Write failing tests** in `test_plugin.py`, with a `FakeUpstreamHost` that records the calling thread and raises `AssertionError("UI thread")` when `begin_update`, `set` or `commit` runs on the thread that delivered `on_message`:
  - `test_mode_detection_upstream`: full upstream gives `automatic`; `preset_bundle` only gives `manual`; neither gives `unavailable`; a host that has only `api_info` and `v1` gives `unavailable`.
  - `test_apply_runs_off_ui_thread_and_commits_one_category`: two print rows plus one filament row are checked and `category="print"`. Expected: exactly one `begin_update("")` on `preset.process`, two `set` calls, one `commit`, nothing on filament.
  - `test_apply_declined_confirmation_applies_nothing`: `ui.message` returns `"no"`. Expected: zero `begin_update` calls and an info status containing "отменено".
  - `test_apply_rejects_stale_values`: the current value of a checked row differs from `old_value`. Expected: no worker started, an error status, analysis rows kept.
  - `test_apply_target_mismatch_does_not_commit`: `tx.target_name` differs from the analysed name. Expected: no `commit`, error status.
  - `test_filament_apply_uses_first_assigned_slot`: slots `["A", "B", "B"]`, edited filament `"B"`. Expected: `preset.filament.begin_update(2)`.
  - `test_filament_apply_without_assigned_slot_refuses`: edited `"C"` is not in the slots. Expected: no worker, error status.
  - `test_commit_value_error_reports_invalid`: `commit` raises `ValueError("bad")`. Expected: error status containing "ничего не применено", rows kept.
  - `test_begin_update_cancel_reports_cancelled`: `begin_update` raises `RuntimeError`. Expected: info status containing "отменено", rows kept.
  - `test_successful_apply_removes_only_that_category_rows`: after a print apply, the filament rows are still listed.
  - `test_unload_during_apply_drops_result`: the worker blocks inside `begin_update`, then `unload()` runs. Expected: unload returns within `SHUTDOWN_TIMEOUT_S + 1` and no status is posted after unload.
  - `test_storage_uses_upstream_plugin_storage`: `host.plugin.storage()` returns a tmp dir. Expected: the config is saved there.
  - `test_register_requests_permissions`: the fake `orca.request_permissions` is called once with `network=["https://generativelanguage.googleapis.com"]` and `threading=True`.
  - `test_gemini_permission_error_message`: the worker raises `PermissionError`. Expected: the error status mentions the "Плагины" dialog and the host, with no traceback text.
  - Update `test_core.py` cases that build `Workspace`/`Analysis` with `revision`.
- [ ] **Step 2: Run** `python3 -m unittest discover -s plugins/ai_assist/tests -t plugins/ai_assist -v`. Expected: the new tests FAIL (missing names, or the `v1` path still used).
- [ ] **Step 3: Implement.** Reading stays on the GUI thread through `workspace_from_bundle` (the former legacy path is now the only path). `_on_apply` validates rows, runs `stale_rows` and resolves the slot on the GUI thread, then submits `apply_category` through the existing `TaskController` with kind `"apply"`. While `_applying` is set, other actions are refused (existing behavior). Map exceptions: `ValueError` from `commit` becomes error "OrcaSlicer отклонил изменения, ничего не применено"; `RuntimeError` from `begin_update` becomes info "Применение отменено, пресеты не изменены"; a target mismatch becomes error "Пресет сменился после анализа — запустите анализ заново"; `PermissionError` becomes error "OrcaSlicer заблокировал доступ: разрешите <host> в окне «Плагины» и повторите". The confirmation text lists `CATEGORY_LABELS[category] / key / old -> new` per row and never includes config secrets. In `register_capabilities`, call `orca.request_permissions(network=["https://generativelanguage.googleapis.com"], threading=True)`. A custom `base_url` relies on the runtime prompt, and README says so. Update `MODE_TEXT[MODE_AUTOMATIC]` to say that each category is applied separately after an OrcaSlicer confirmation. Set the header `version = "1.1.0"`.
- [ ] **Step 4: Run** the suite. Expected: all tests pass.
- [ ] **Step 5: Update docs.** README and `docs/AI_ASSIST.md`: requirements "OrcaSlicer 2.5.0+", per-category apply, permissions requested at install, manual mode on older hosts. Remove any mention of the fork build or API v1.
- [ ] **Step 6: Commit** `Run AI Assist on the OrcaSlicer 2.5.0 preset API`.

### Task 3: Filament QR on the upstream preset API

**Files:**
- Modify: `plugins/filament_qr/orca_filament_qr.py`: `HostCapabilities`, `WorkspaceState`, `read_workspace`, `attempt_pending`, `Controller.process_queue`, `FilamentQRPage.on_load`, `FilamentQRPlugin.register_capabilities`, header `version`
- Modify: `plugins/filament_qr/tests/test_host.py`, `test_controller.py`, `helpers.py`
- Modify: `plugins/filament_qr/README.md`

**Interfaces:**
- Consumes: upstream facts above.
- Produces:
  - `HostCapabilities.discover(orca_module)`: `mode = 'automatic'` when `callable(getattr(host.preset.filament, 'begin_update', None))` and `preset_bundle` is callable, otherwise `'manual'`. `storage = callable(getattr(host.plugin, 'storage', None))`. The `features` field is removed.
  - `WorkspaceState`: the `revision` field is removed. `read_workspace` always uses `preset_bundle()` on the GUI thread.
  - `select_in_slot(host, slot: int, profile: str) -> Tuple[str, str]` (status, message). Runs on a worker. It calls `ui.message(..., buttons="yes_no")`, then `host.preset.filament.begin_update(slot + 1, profile)` and `commit()`, and returns `'done'` when `tx.target_name == profile`. `RuntimeError` gives `'cancelled'`; `ValueError` gives `'needs_attention'` with the `RESULT_MESSAGES['incompatible']` text; `PermissionError` and other errors give `'needs_attention'`.
  - `Controller` holds at most one selection worker (`self._selecting: Optional[threading.Thread]`). `process_queue()` on the GUI thread does the existing pre-checks (`slot` range, already selected, profile listed) and then either starts the worker for one pending item or leaves items queued. The worker result is marked in the store under the store lock. The next `tick` refreshes the workspace.
  - In `register_capabilities`: `orca.request_permissions(network_socket=["127.0.0.1", "0.0.0.0"], threading=True)`.

- [ ] **Step 1: Write failing tests** with a fake upstream host that enforces the off-UI-thread rule as in Task 2:
  - `test_caps_upstream_automatic`, `test_caps_bundle_only_manual`, `test_caps_v1_only_is_manual_without_storage`.
  - `test_select_runs_off_ui_thread`: a queued scan becomes `begin_update(2, "PLA Basic")` on a non-GUI thread after `tick`; status `done`.
  - `test_select_declined_marks_cancelled`: `ui.message` returns `"no"`. Expected: `begin_update` is not called and the status is `cancelled`.
  - `test_select_cancelled_prompt_marks_cancelled`: `begin_update` raises `RuntimeError`. Expected: `cancelled`.
  - `test_select_value_error_marks_needs_attention`.
  - `test_single_selection_in_flight`: two queued items. Expected: exactly one worker while the first blocks; the second is still `queued`.
  - `test_unload_during_select_drops_result`: unload while the worker blocks. Expected: `shutdown()` returns within 4 s and no page message after unload.
  - `test_server_bind_permission_error_message`: `server.start` raises `PermissionError`. Expected: `server_error` mentions the "Плагины" dialog.
  - `test_storage_uses_upstream_plugin_storage`.
  - Update existing host/controller tests that used `v1.presets.snapshot` or `commit`.
- [ ] **Step 2: Run** `python3 -m unittest discover -s plugins/filament_qr/tests -t plugins/filament_qr -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** `FilamentHttpServer.start` maps `PermissionError` to `RuntimeError` with the text "OrcaSlicer заблокировал сетевой доступ плагина: разрешите его в окне «Плагины»". `Controller.shutdown()` joins the selection worker for at most 3 s and sets a closed flag that suppresses late store and page updates. Remove the `MAX_AUTO_ATTEMPTS` conflict branch, which has no upstream equivalent; the stale slot check is done by upstream `commit()`. Update the "storage unavailable" text to "нужен OrcaSlicer 2.5.0 или новее". Set the header `version = "1.1.0"`.
- [ ] **Step 4: Run** the suite. Expected: all tests pass.
- [ ] **Step 5: Update the README**: requirements, permissions, manual mode, no API v1.
- [ ] **Step 6: Commit** `Run Filament QR on the OrcaSlicer 2.5.0 preset API`.

### Task 4: Compatibility manifest and release artifacts

**Files:**
- Create: `plugins/compatibility.json`
- Create: `scripts/plugin_release.py`
- Create: `tests/scripts/test_plugin_release.py`

**Interfaces:**
- Produces:
  - `plugins/compatibility.json` schema (validated in code, no JSON-Schema dependency): `{"manifest_version": 1, "host": {"product": "OrcaSlicer", "tag": "v2.5.0", "commit": "<40 hex>", "required_api": ["orca.host.preset", "orca.host.plugin.storage", "orca.host.ui.message", "orca.request_permissions"]}, "plugins": [{"id": "ai_assist", "file": "plugins/ai_assist/orca_ai_assist.py", "version": "1.1.0", "min_host": "2.5.0", "sha256": "<64 hex>", "platforms": {"windows-x64": "passed|failed|not-tested", "linux-x64": ..., "macos-arm64": ...}}, ...]}`
  - `python3 scripts/plugin_release.py check`: exits non-zero when a field is missing or malformed, when the header `version` differs from the manifest, when a sha256 is stale, when a status is outside the allowed three, or when a plugin file references `orca.host.v1` or `api_info`.
  - `python3 scripts/plugin_release.py build --out DIR`: runs `check`, copies the two `.py` files to `DIR`, writes `DIR/SHA256SUMS` (sorted, `<hash>  <name>`) and `DIR/compatibility.json`. The output is deterministic; a rerun gives identical bytes.
  - `python3 scripts/plugin_release.py update-hashes`: rewrites only the `sha256` fields.

- [ ] **Step 1: Write failing tests** `test_plugin_release.py`, using tmp copies of the manifest and plugins: `test_check_passes_on_repo`, `test_check_rejects_stale_hash`, `test_check_rejects_version_mismatch`, `test_check_rejects_unknown_status`, `test_check_rejects_v1_reference`, `test_check_rejects_bad_commit_sha`, `test_build_is_deterministic`, `test_not_tested_is_default_for_new_platform`.
- [ ] **Step 2: Run** `python3 -m unittest tests/scripts/test_plugin_release.py -v`. Expected: FAIL (the script is missing).
- [ ] **Step 3: Implement** with the standard library only. Read the header version with the regex `^# version = "([^"]+)"$` inside the `# /// script` block. All platforms start as `not-tested`.
- [ ] **Step 4: Run** the tests plus `python3 scripts/plugin_release.py update-hashes && python3 scripts/plugin_release.py check`. Expected: PASS, exit 0.
- [ ] **Step 5: Commit** `Add plugin compatibility manifest and release tooling`.

### Task 5: CI and release documentation

**Files:**
- Create: `.github/workflows/plugins.yml`
- Create: `docs/release/plugins.md`
- Modify: `README.md` (a short fork note at the top: what this branch ships and how to install the plugins into official OrcaSlicer 2.5.0)

**Interfaces:**
- Consumes: Task 4 commands and both suites.
- Produces: workflow jobs `test` (matrix `ubuntu-latest`, `windows-latest`, `macos-latest`; Python 3.12; both suites, `tests/scripts`, `plugin_release.py check`) and `package` (needs `test`; `plugin_release.py build --out dist`; uploads artifact `orca-plugins-<sha7>`). The `release` job runs only on tags `plugins-v*`. It attaches `dist/*` to the GitHub release with the same tag and fails if the release already has assets for a different commit. It uses `actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-artifact@v4` and `softprops/action-gh-release@v2`, pinned to major versions like the repo's other workflows.

- [ ] **Step 1: Write the workflow.** Triggers: `push` on `plugins-v2.5.0`, `pull_request` touching `plugins/**`, `scripts/plugin_release.py`, `tests/scripts/**` or the workflow, and `push` of tags `plugins-v*`. Upstream workflows inherited from v2.5.0 stay untouched.
- [ ] **Step 2: Validate locally.** `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/plugins.yml'))"` (install PyYAML into a temp venv if it is missing) and `actionlint` if available. Run every command from the workflow's `test` job locally. Expected: all exit 0.
- [ ] **Step 3: Write `docs/release/plugins.md`.** Cover the install steps (Plugins dialog → install `.py`), the permissions prompted, the manifest meaning (a `not-tested` status is honest, not a failure), the manual check list from Task 6, and the release procedure (bump versions, run `update-hashes`, tag `plugins-vX.Y.Z`). Pushing tags is the user's action.
- [ ] **Step 4: Commit** `Add plugin CI and release docs`.

### Task 6: Manual verification on official OrcaSlicer 2.5.0

The executor cannot run official OrcaSlicer on this aarch64 board. This task is a hand-off checklist. It is complete when the user reports the results and the manifest records them.

- [ ] **Step 1:** On Windows x64 with official OrcaSlicer 2.5.0, install both `.py` files from the `package` artifact and accept the permission prompts.
- [ ] **Step 2: AI Assist.** Run an analysis with a valid key, check one process row and one filament row, then apply "Процесс": a confirmation appears, Yes applies, and the process tab is dirty with the new value. Apply "Пластик" and answer No: nothing changes. Edit a checked value by hand and press apply: the stale message appears.
- [ ] **Step 3: Filament QR.** Create a spool, scan it from a phone over LAN for slot 2: a confirmation appears, Yes selects the preset into slot 2, and an unsaved-edit prompt appears if the filament tab was dirty.
- [ ] **Step 4:** Disable and re-enable each plugin, then close OrcaSlicer during a pending Gemini request. Expected: no hang and no crash.
- [ ] **Step 5:** Record the results in `plugins/compatibility.json` `platforms.windows-x64` (`passed` or `failed`), run `check`, and commit `Record OrcaSlicer 2.5.0 Windows verification`.
