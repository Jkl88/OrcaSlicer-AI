# OrcaSlicer Plugin Host API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a provider-neutral, versioned OrcaSlicer host API for feature discovery, plugin-private storage, immutable preset snapshots, and user-confirmed atomic preset transactions.

**Architecture:** Keep detached value types and validation in focused C++ units under `src/slic3r/plugin/host/`; bind one additive `orca.host.v1` namespace through pybind11. A GUI coordinator owns main-thread execution, confirmation, native preset selection, rollback, and refresh; plugins submit immutable operation lists and never receive native pointers.

**Tech Stack:** C++17, wxWidgets, pybind11, nlohmann JSON, Catch2, CMake.

**Spec:** `docs/superpowers/specs/2026-11-10-orcaslicer-plugin-host-api-design.md`

## Global Constraints

- Primary compatibility target is OrcaSlicer v2.5.0 tag `40ecc66f78ed72dc368e0c1ebe40d77f00cc8a19`.
- The API is additive and keeps existing unversioned plugin bindings working.
- No Gemini-, AI-, Telegram-, QR-, or inventory-specific code enters the host patch.
- Existing `.3mf` and preset formats do not change.
- Plugins never receive `Tab*`, `Preset*`, `DynamicPrintConfig*`, arbitrary paths, or credential-bearing values.
- Third-party mutations require `interaction="require_user_confirmation"`; v1 has no silent commit.
- All mutations execute on the GUI thread and either fully apply or roll back.
- Top-level windows use `SetSizerAndFit(sizer)` as required by project style.
- New tests belong to `tests/slic3rutils` and follow `tests/AGENTS.md`.

## Review Focus

- A stale snapshot revision must return `conflict` without showing confirmation or changing state; Task 4 adds the test.
- A mixed transaction with one invalid value must return `invalid` and apply none of the valid operations; Task 5 adds the rollback test.
- Sensitive keys such as `printhost_apikey` must be absent even when explicitly requested; Task 3 adds the deny-list test.
- A filament slot outside the current slot list or an incompatible preset must return a structured failure without changing selection; Task 6 adds both tests.
- Plugin storage calls without an active plugin audit context or with traversal-like identities must fail closed; Task 2 adds both tests.

---

### Task 1: Versioned API Discovery

**Files:**
- Create: `src/slic3r/plugin/host/PluginHostApi.hpp`
- Create: `src/slic3r/plugin/host/PluginHostApi.cpp`
- Modify: `src/slic3r/plugin/host/PluginHost.cpp:7-24`
- Modify: `src/slic3r/plugin/host/PluginHostBindings.hpp:7-16`
- Modify: `src/slic3r/CMakeLists.txt:624-637`
- Create: `tests/slic3rutils/test_plugin_host_workspace.cpp`
- Modify: `tests/slic3rutils/CMakeLists.txt:2-18`

**Interfaces:**
- Produces: `PluginHostApi::api_info() -> nlohmann::json`.
- Produces Python: `orca.host.api_info() -> dict` and namespace `orca.host.v1`.
- Exact v1 features at this task: `plugin_storage.v1`, `preset_snapshot.v1`, `preset_transaction.v1`, `preset_select.v1`, `preset_values.v1`.

- [ ] **Step 1: Write the failing discovery test**

Add `TEST_CASE("Plugin host API advertises stable versioned features", "[PluginHost][Python]")`. Assert `host.api_info()` returns `api_version == "1.0"`, a non-empty `host_version`, the five exact feature strings, and that `host.v1` exists. Split every Catch2 condition into its own assertion.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `cmake --build build --config RelWithDebInfo --target slic3rutils_tests && ctest --test-dir build/tests -C RelWithDebInfo -R "Plugin host API advertises stable versioned features" --output-on-failure`

Expected: FAIL because `api_info`/`v1` is absent.

- [ ] **Step 3: Implement discovery and namespace registration**

Declare `class PluginHostApi` with `static nlohmann::json api_info();` and `static void RegisterBindings(pybind11::module_& host);`. Build the result from constants, expose it through `json_to_py`, create `host.def_submodule("v1")`, and register this unit before the domain registrars in `PluginHost::RegisterBindings`.

- [ ] **Step 4: Run the focused test**

Expected: PASS with all exact feature identifiers.

- [ ] **Step 5: Commit**

```bash
git add src/slic3r/plugin/host/PluginHostApi.* src/slic3r/plugin/host/PluginHost.cpp \
  src/slic3r/plugin/host/PluginHostBindings.hpp src/slic3r/CMakeLists.txt \
  tests/slic3rutils/test_plugin_host_workspace.cpp tests/slic3rutils/CMakeLists.txt
git commit -m "Add versioned plugin host API discovery

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 2: Plugin-Private Storage

**Files:**
- Create: `src/slic3r/plugin/host/PluginHostStorage.hpp`
- Create: `src/slic3r/plugin/host/PluginHostStorage.cpp`
- Modify: `src/slic3r/plugin/host/PluginHostApi.cpp`
- Modify: `src/slic3r/plugin/PluginManager.hpp`
- Modify: `src/slic3r/plugin/PluginManager.cpp`
- Modify: `src/slic3r/GUI/PluginsDialog.hpp`
- Modify: `src/slic3r/GUI/PluginsDialog.cpp`
- Modify: `src/slic3r/CMakeLists.txt:624-637`
- Modify: `tests/slic3rutils/test_plugin_host_workspace.cpp`

**Interfaces:**
- Consumes: `PluginAuditManager::current_plugin()`.
- Produces C++: `boost::filesystem::path plugin_storage_path(const std::string& plugin_key)`.
- Produces Python: `orca.host.v1.plugin_storage() -> str`.

- [ ] **Step 1: Write failing storage identity and isolation tests**

Add tests that enter `ScopedPluginAuditContext("plugin-one")` and `("plugin-two")`, call `v1.plugin_storage()`, and assert canonical paths are distinct children of `<data_dir>/orca_plugins_data/`. Assert the directory exists and a file can be written there. Add tests that no active context raises `RuntimeError`, and identities containing `../`, separators, or an empty escaped result do not escape the root.

- [ ] **Step 2: Run the focused storage tests**

Expected: FAIL because `plugin_storage()` is absent.

- [ ] **Step 3: Implement storage path creation and registration**

Use `filesystem_safe_escape(plugin_key)`, reject empty current context, create `<data_dir>/orca_plugins_data/<escaped-key>`, canonicalize it, verify `is_inside_allowed_root`, and return a UTF-8 path string. Register the root with the audit manager only through the existing `data_dir()` allowance; do not weaken denied filenames.

- [ ] **Step 4: Run storage and audit suites**

Run: `ctest --test-dir build/tests -C RelWithDebInfo -R "Plugin host private storage|PluginAudit" --output-on-failure`

Expected: PASS; existing denied-config tests stay green.

- [ ] **Step 5: Add explicit retained-data cleanup to the existing plugin management UI**

Extend the existing uninstall/remove action with a separate unchecked-by-default “Remove plugin data” choice. The host must retain `<data_dir>/orca_plugins_data/<id>` by default, show the exact plugin identity being removed, require user confirmation for deletion, reject symlink/root escapes through the same canonical containment check, and report deletion failure without pretending uninstall removed the data.

- [ ] **Step 6: Test retain-by-default and confirmed cleanup**

Add tests for uninstall retaining data, confirmed cleanup deleting only the active plugin directory, cancellation retaining it, symlink escape rejection, and one plugin being unable to remove another plugin's directory.

- [ ] **Step 7: Commit**

```bash
git add src/slic3r/plugin/host/PluginHostStorage.* src/slic3r/plugin/host/PluginHostApi.cpp \
  src/slic3r/plugin/PluginManager.* src/slic3r/GUI/PluginsDialog.* src/slic3r/CMakeLists.txt \
  tests/slic3rutils/test_plugin_host_workspace.cpp
git commit -m "Add isolated plugin data storage

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 3: Detached Preset Snapshots

**Files:**
- Create: `src/slic3r/plugin/host/PluginPresetSnapshot.hpp`
- Create: `src/slic3r/plugin/host/PluginPresetSnapshot.cpp`
- Create: `src/slic3r/plugin/host/PluginHostPresetWorkspace.cpp`
- Modify: `src/slic3r/plugin/host/PluginHostBindings.hpp`
- Modify: `src/slic3r/plugin/host/PluginHost.cpp`
- Modify: `src/slic3r/CMakeLists.txt:624-637`
- Modify: `tests/slic3rutils/test_plugin_host_workspace.cpp`

**Interfaces:**
- Produces: `PresetSnapshotRequest { std::map<Preset::Type, std::vector<std::string>> config_keys; }`.
- Produces: `PresetWorkspaceSnapshot make_preset_snapshot(const PresetBundle&, bool project_dirty, bool presets_dirty, const PresetSnapshotRequest&)`.
- Produces: `std::string compute_preset_revision(const PresetWorkspaceSnapshot&)`.
- Produces Python: `orca.host.v1.presets.snapshot(config_keys: dict[str, list[str]] = {}) -> dict`.

- [ ] **Step 1: Write failing detached snapshot tests**

Construct an in-memory `PresetBundle`, two filament slots, dirty state, and requested keys. Assert returned JSON-compatible values include `revision`, `resources`, `selected`, `edited`, `filament_slots`, `active_printer`, `project_dirty`, `presets_dirty`, and only requested known values. Change one selected slot and assert the revision changes. Destroy/change source config after capture and assert the detached snapshot does not change.

- [ ] **Step 2: Add the sensitive-key regression test**

Request `printhost_apikey`, `print_host`, a nonexistent key, and `layer_height`. Assert credential keys are omitted with a diagnostic, the nonexistent key is omitted, and `layer_height` is serialized when present.

- [ ] **Step 3: Run snapshot tests and verify failure**

Expected: FAIL because snapshot types and binding are absent.

- [ ] **Step 4: Implement deterministic snapshot construction**

Use plain owning structs and `nlohmann::json` conversion. Include stable sorted fields in the revision input; hash with a deterministic project-available hash rather than `std::hash`. Add a centralized sensitive-key predicate that rejects API key/token/password/credential keys, including exact `printhost_apikey`.

- [ ] **Step 5: Bind `host.v1.presets.snapshot`**

Create the `presets` submodule once. Resolve the live bundle/plater with the same guarded app access pattern as `PluginHostApp.cpp`. Marshal to the GUI thread using the existing `PluginHostUi` main-thread helper extracted into a reusable internal utility if necessary; return detached Python dictionaries after releasing native references.

- [ ] **Step 6: Run snapshot and existing host API tests**

Expected: PASS; `test_plugin_host_api.cpp` remains green.

- [ ] **Step 7: Commit**

```bash
git add src/slic3r/plugin/host/PluginPresetSnapshot.* \
  src/slic3r/plugin/host/PluginHostPresetWorkspace.cpp \
  src/slic3r/plugin/host/PluginHostBindings.hpp src/slic3r/plugin/host/PluginHost.cpp \
  src/slic3r/CMakeLists.txt tests/slic3rutils/test_plugin_host_workspace.cpp
git commit -m "Expose immutable preset workspace snapshots

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 4: Transaction Request Parsing and Revision Conflicts

**Files:**
- Create: `src/slic3r/plugin/host/PluginPresetTransaction.hpp`
- Create: `src/slic3r/plugin/host/PluginPresetTransaction.cpp`
- Modify: `src/slic3r/CMakeLists.txt:624-637`
- Modify: `tests/slic3rutils/test_plugin_host_workspace.cpp`

**Interfaces:**
- Produces: `PresetTransactionRequest { std::string expected_revision; std::string interaction; std::vector<PresetOperation> operations; }`.
- `PresetOperation` is a tagged value for `SelectPresetOperation` and `SetValuesOperation`.
- Produces: `PresetTransactionResult { PresetTransactionStatus status; std::string revision; json affected; json diagnostics; }`.
- Produces: `parse_transaction_request(const nlohmann::json&)` and `validate_transaction_shape(...)`.

- [ ] **Step 1: Write failing request-shape tests**

Cover an empty operation list, unknown operation, unknown preset type, missing `expected_revision`, unsupported `interaction="silent"`, invalid scope, duplicate key assignment, and valid `select`/`set_values` operations. Assert exact statuses/diagnostic codes rather than exception text.

- [ ] **Step 2: Write the stale revision test**

Given request revision `A` and current revision `B`, assert result status `conflict`, `affected` is empty, and no confirmation/apply callback is invoked.

- [ ] **Step 3: Run tests and verify failure**

Expected: FAIL because transaction request types are absent.

- [ ] **Step 4: Implement strict parsing and early revision validation**

Accepted operation JSON is exactly:

```json
{"op":"select","preset_type":"filament","name":"Generic PLA","scope":{"slot":0}}
{"op":"set_values","preset_type":"print","scope":{"edited":true},"values":{"layer_height":"0.16"}}
```

Reject unknown fields only where they create ambiguity; preserve diagnostics as `{code, operation_index, field, message}`.

- [ ] **Step 5: Run the focused tests**

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/slic3r/plugin/host/PluginPresetTransaction.* src/slic3r/CMakeLists.txt \
  tests/slic3rutils/test_plugin_host_workspace.cpp
git commit -m "Define preset transaction request contract

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 5: Atomic Typed Preset Value Staging

**Files:**
- Modify: `src/slic3r/plugin/host/PluginPresetTransaction.hpp`
- Modify: `src/slic3r/plugin/host/PluginPresetTransaction.cpp`
- Modify: `tests/slic3rutils/test_plugin_host_workspace.cpp`

**Interfaces:**
- Produces: `stage_value_changes(PresetBundle&, const std::vector<SetValuesOperation>&) -> StagedPresetChanges`.
- Produces: `apply_staged_value_changes(PresetBundle&, StagedPresetChanges&)` and `rollback_staged_value_changes(...)`.
- Uses native `ConfigSubstitutionContext` and `DynamicPrintConfig::set_deserialize` on copies before live mutation.

- [ ] **Step 1: Write failing validation tests**

Cover valid print/filament/printer changes, unknown key, sensitive key, malformed serialized value, unsupported preset type, and scope other than `{"edited": true}`. Assert old/new serialized values are captured for preview.

- [ ] **Step 2: Write mixed-operation atomicity test**

Submit one valid `layer_height` and one invalid key/value. Assert status `invalid`, the edited preset's serialized `layer_height` remains unchanged, and diagnostics identify only the rejected operation.

- [ ] **Step 3: Write rollback-on-apply-failure test**

Inject an apply callback that fails after the first category. Assert all affected edited configs equal their pre-transaction copies and status is `error`.

- [ ] **Step 4: Run tests and verify failure**

Expected: FAIL because staging is absent.

- [ ] **Step 5: Implement copy-validate-stage-apply-rollback**

Copy each affected edited `DynamicPrintConfig`, apply all serialized values to copies first, and store original full configs. Publish live copies only after all validation succeeds. Never save presets automatically.

- [ ] **Step 6: Run the focused tests**

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/slic3r/plugin/host/PluginPresetTransaction.* \
  tests/slic3rutils/test_plugin_host_workspace.cpp
git commit -m "Stage plugin preset edits atomically

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 6: Native Filament Slot Selection Adapter

**Files:**
- Modify: `src/slic3r/GUI/Plater.hpp` (declare host-safe selection entry point near existing filament change methods)
- Modify: `src/slic3r/GUI/Plater.cpp:10468-10492,17599-17618`
- Modify: `src/slic3r/plugin/host/PluginPresetTransaction.cpp`
- Modify: `tests/slic3rutils/test_plugin_host_workspace.cpp`

**Interfaces:**
- Produces GUI: `FilamentSelectionResult Plater::select_filament_preset_for_plugin(size_t slot, const std::string& preset_name)`.
- Result distinguishes `applied`, `cancelled`, `invalid_slot`, `not_found`, and `incompatible`.
- Reuses `PresetBundle::set_filament_preset`, project-dirty update, selection export, sidebar refresh, and `Plater::on_filament_change` in the same order as the combo-box path.

- [ ] **Step 1: Extract the existing combo-box side effects behind a tested adapter seam**

First add a test double around the selection dependencies and assert one valid selection calls: set slot, mark project dirty, export selections, refresh dynamic list, then notify filament change. Assert no side effect occurs before validation.

- [ ] **Step 2: Add invalid slot, missing preset, and incompatible preset tests**

For each case assert the corresponding structured result and unchanged `filament_presets`.

- [ ] **Step 3: Add slot-zero dirty-state behavior test**

Use an injected confirmation/selection callback to model `Tab::select_preset()` cancellation. Assert cancellation preserves both the edited preset and slot selection. For slot greater than zero, assert no edited-preset discard is required because the edited tab selection is not replaced.

- [ ] **Step 4: Implement and reuse the adapter from the existing UI handler**

Move only the common side effects; keep combo-specific badge/flushing logic in the combo handler. The plugin path must resolve internal preset names, not display aliases.

- [ ] **Step 5: Run focused and Plater-related tests**

Expected: PASS with no selection regression.

- [ ] **Step 6: Commit**

```bash
git add src/slic3r/GUI/Plater.hpp src/slic3r/GUI/Plater.cpp \
  src/slic3r/plugin/host/PluginPresetTransaction.cpp \
  tests/slic3rutils/test_plugin_host_workspace.cpp
git commit -m "Add safe plugin filament slot selection

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 7: User-Confirmed GUI Transaction Coordinator

**Files:**
- Create: `src/slic3r/GUI/PluginPresetTransactionDialog.hpp`
- Create: `src/slic3r/GUI/PluginPresetTransactionDialog.cpp`
- Create: `src/slic3r/plugin/host/PluginPresetTransactionCoordinator.hpp`
- Create: `src/slic3r/plugin/host/PluginPresetTransactionCoordinator.cpp`
- Modify: `src/slic3r/plugin/host/PluginHostPresetWorkspace.cpp`
- Modify: `src/slic3r/CMakeLists.txt`
- Modify: `tests/slic3rutils/test_plugin_host_workspace.cpp`

**Interfaces:**
- Produces: `PresetTransactionResult commit_preset_transaction(const PresetTransactionRequest&)`.
- Produces Python: `orca.host.v1.presets.commit(request: dict) -> dict`.
- Dialog constructor consumes plugin identity and staged old/new rows; result is confirm/cancel only.

- [ ] **Step 1: Write failing coordinator tests with injected UI hooks**

Test confirm, cancel, stale revision, validation failure, selection cancellation, and exception during GUI refresh. Assert the confirmation callback is invoked only after complete validation and receives requesting plugin identity plus exact old/new rows.

- [ ] **Step 2: Add no-GUI and forbidden-context tests**

Before wx app initialization and during simulated shutdown/slicing context, assert Python receives a structured `unsupported` result rather than deadlock or crash.

- [ ] **Step 3: Run tests and verify failure**

Expected: FAIL because coordinator/binding is absent.

- [ ] **Step 4: Implement main-thread commit flow**

Order: capture current revision → reject conflict → parse/validate all operations → stage values and selections → show one confirmation dialog → apply selection/value stages → call `Tab::update_dirty()` and `reload_config()` for affected categories → update plater/sidebar → compute and return new revision. On failure, restore configs and slot names, refresh once, return `error`.

- [ ] **Step 5: Implement the native preview dialog**

Show plugin name, affected category/preset/slot, old value, proposed value, compatibility warning, and explicit Apply/Cancel controls. Use `SetSizerAndFit`. Do not expose Save or silent apply; existing `Tab` unsaved-change flow remains authoritative when selection requires it.

- [ ] **Step 6: Bind `presets.commit` with strict dict conversion**

Use `py_to_json`/`json_to_py`, release the GIL while waiting for GUI execution, restore plugin audit context on the GUI call, and never retain Python objects in the coordinator.

- [ ] **Step 7: Run all slic3rutils tests**

Run: `ctest --test-dir build/tests -C RelWithDebInfo -R slic3rutils --output-on-failure`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/slic3r/GUI/PluginPresetTransactionDialog.* \
  src/slic3r/plugin/host/PluginPresetTransactionCoordinator.* \
  src/slic3r/plugin/host/PluginHostPresetWorkspace.cpp src/slic3r/CMakeLists.txt \
  tests/slic3rutils/test_plugin_host_workspace.cpp
git commit -m "Commit plugin preset transactions through native UI

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 8: Contract Examples, Stubs, and Host Verification

**Files:**
- Create: `docs/plugin-host-api-v1.md`
- Create: `sandboxes/orca_preset_transaction_plugin_any.py`
- Modify: Python stub generation inputs/output used by the existing build (locate generated `orca` stubs before editing)
- Modify: `tests/slic3rutils/test_plugin_host_workspace.cpp`

**Interfaces:**
- Consumes all v1 interfaces.
- Produces a copy-pastable feature-detection/snapshot/commit example and accurate generated type hints.

- [ ] **Step 1: Add a Python smoke-contract test**

Import `orca`, check all five advertised features have corresponding callable surfaces, and execute malformed/unsupported requests to prove they return structured results without native exceptions.

- [ ] **Step 2: Write API documentation and example plugin**

Document exact request/result JSON, zero-based filament slots, sensitive-key omission, revision conflicts, confirmation semantics, fallback behavior, and the fact that v1 never saves presets.

- [ ] **Step 3: Regenerate or update stubs through the repository's canonical stub command**

Do not hand-edit generated files if a generator exists. Verify `api_info`, `v1.plugin_storage`, `v1.presets.snapshot`, and `v1.presets.commit` signatures.

- [ ] **Step 4: Run full host verification**

Run:

```bash
cmake --build build --config RelWithDebInfo --target slic3rutils_tests
ctest --test-dir build/tests -C RelWithDebInfo -R slic3rutils --output-on-failure
git diff --check
```

Expected: all PASS and no whitespace errors.

- [ ] **Step 5: Request mandatory C++ review**

Use the `cpp-reviewer` agent on the complete host API diff. Address every confirmed memory-safety, threading, rollback, ABI, or cross-platform finding and rerun the focused suite.

- [ ] **Step 6: Commit**

```bash
git add docs/plugin-host-api-v1.md sandboxes/orca_preset_transaction_plugin_any.py \
  tests/slic3rutils/test_plugin_host_workspace.cpp <generated-stub-paths>
git commit -m "Document plugin preset workspace API

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```
