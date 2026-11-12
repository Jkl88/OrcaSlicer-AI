# OrcaSlicer Plugin Host API and Plugin Migration Design

**Status:** Draft for user review  
**Date:** 2026-11-10  
**Primary compatibility target:** OrcaSlicer v2.5.0 (`40ecc66f78ed72dc368e0c1ebe40d77f00cc8a19`)  
**Upstream contribution target:** current `SoftFever/OrcaSlicer` `main` at implementation time

## 1. Purpose

Move the existing AI Assist functionality out of the OrcaSlicer executable and into an independently installable and updateable Python plugin. Extend the public plugin host with a small, versioned, provider-neutral API that also supports the `orca_filament` plugin.

Until the API is accepted upstream, distribute:

1. a minimal source patch for each supported stable OrcaSlicer release;
2. prebuilt OrcaSlicer packages containing that patch;
3. AI Assist and `orca_filament` as separately versioned plugins.

After upstream adoption, the same plugins must run on official OrcaSlicer builds without a private fork. On hosts without the new API, both plugins must load in a reduced manual mode rather than fail.

## 2. Success criteria

The work is complete when:

- AI Assist no longer requires AI-specific changes in `GUI_App`, `MainFrame`, `Preferences`, or `libslic3r_gui` build lists.
- Both plugins load through OrcaSlicer's existing Python plugin manager.
- The host API contains no Gemini-, AI-, Telegram-, QR-, or filament-inventory-specific method.
- Plugins detect individual host features instead of inferring support from the OrcaSlicer version string.
- Preset mutations are validated, user-mediated, revision-checked, and atomic from the plugin's perspective.
- Filament preset selection uses the same native dirty-state and compatibility handling as the OrcaSlicer UI.
- Neither plugin directly edits preset files, `OrcaSlicer.conf`, nor `.3mf` archives.
- Installing an official build without the extension leaves both plugins usable in documented manual modes.
- The source patch applies cleanly to the supported stable tag and is tested in CI.
- The generic API is submitted to the official OrcaSlicer repository as a focused pull request with tests and Python examples.

## 3. Current-state findings

### 3.1 Existing plugin platform

OrcaSlicer already provides embedded Python plugins, `.py` and `.whl` installation, Pages and Script capabilities, lifecycle callbacks, per-capability configuration, and read-only access to preset collections and edited presets.

The public preset bridge is read-only. It does not provide a supported way to select a filament for a particular slot or modify the current print, filament, or printer preset.

The host also lacks explicit feature negotiation and a host-created, plugin-scoped data directory.

### 3.2 AI Assist

The existing native AI Assist implementation includes working preset collection, Gemini requests, recommendation parsing, and direct mutation of edited presets. It also contains prototype monitoring, webcam, Telegram, and Moonraker functionality.

The current native implementation is not suitable for release as-is:

- network requests block the GUI thread;
- the print-monitor prompt and parser disagree on the anomaly field;
- “Apply selected changes” applies every returned change;
- preset mutation is not atomic;
- a printer API key is included in data sent to Gemini;
- direct webcam requests may receive a Moonraker key for a different host;
- Telegram stop callbacks do not sufficiently authenticate the expected chat/user;
- shutdown may wait on blocking network operations;
- AI-specific automated tests are absent.

### 3.3 `orca_filament`

`orca_filament` is currently a single-file Python prototype (`0.1.0`) with QR label creation, a local SQLite spool database, a LAN web interface, PIN authentication, QR decoding, and a pending scan state.

It is not loadable unchanged on OrcaSlicer v2.5.0 because it calls APIs that do not exist:

- `orca.request_permissions(...)`;
- `orca.host.plugin.storage()`.

It reads preset state through the current host bridge but does not actually select a scanned filament preset. The pending scan queue is memory-only. The README references tests and files that are not present.

## 4. Scope

### 4.1 Host patch v1

The first host patch provides:

1. feature and API discovery;
2. plugin-scoped persistent storage;
3. immutable preset snapshots with revision tokens;
4. user-mediated preset transactions;
5. filament selection by slot;
6. typed value updates for edited print, filament, and printer presets;
7. structured operation results;
8. C++ tests and Python contract examples.

### 4.2 AI Assist plugin

The release plugin keeps only finished and verified functionality:

- inspect the current preset state;
- analyze settings through Gemini;
- analyze a user-entered print defect description;
- display structured recommendations with old and proposed values;
- let the user select individual recommendations;
- validate and apply selected recommendations through the host transaction API;
- provide a manual copy mode when mutation support is unavailable;
- optionally monitor a configured camera after the monitor contract is fixed;
- optionally send authenticated Telegram notifications;
- optionally request a Moonraker pause/cancel only through a protected confirmation flow.

Features that do not pass their tests and security requirements are omitted from the release UI and documentation rather than shipped as placeholders.

### 4.3 `orca_filament` plugin

The release plugin keeps:

- spool identifiers;
- SVG QR labels;
- a local spool database;
- PIN-authenticated LAN access;
- QR decoding;
- selection of the mapped filament preset in a specified slot through the host API;
- a manual selection fallback when the API is unavailable.

The first release does not claim edit/delete UI, backup/restore, TLS termination, batch label printing, inventory synchronization, or other unimplemented features.

### 4.4 Out of scope for the host PR

The upstream host PR does not contain:

- either plugin;
- any model-provider client;
- Gemini prompts or response parsing;
- camera, Telegram, Moonraker, QR, SQLite, or HTTP-server code;
- direct preset or `.3mf` file manipulation;
- binary patching of installed executables;
- plugin-specific methods such as `apply_ai_changes` or `select_qr_filament`.

## 5. Architecture

```text
┌──────────────────────────────┐    ┌──────────────────────────────┐
│ AI Assist plugin             │    │ orca_filament plugin         │
│ UI, Gemini, monitor, alerts  │    │ QR, SQLite, LAN UI           │
└──────────────┬───────────────┘    └──────────────┬───────────────┘
               │ feature-checked Python API         │
               └─────────────────┬───────────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│ Versioned OrcaSlicer Plugin Host API                             │
│ api_info · plugin_storage · preset snapshot · transaction        │
└───────────────────────────────┬──────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│ Existing native OrcaSlicer flows                                │
│ PresetBundle · Tab dirty handling · GUI refresh · project state  │
└──────────────────────────────────────────────────────────────────┘
```

The host owns all OrcaSlicer state, thread marshalling, validation, confirmation, rollback, and GUI refresh. Plugins own their domain logic and private data.

## 6. Versioning and discovery

### 6.1 Capability query

The root host exposes a small discovery call:

```python
info = orca.host.api_info()
```

Conceptual result:

```python
{
    "api_version": "1.0",
    "host_version": "2.5.0",
    "features": [
        "plugin_storage.v1",
        "preset_snapshot.v1",
        "preset_transaction.v1",
        "preset_select.v1",
        "preset_values.v1"
    ]
}
```

Feature identifiers are additive contracts. A plugin enables only functionality whose feature identifier is present. An OrcaSlicer product version must never substitute for feature detection.

### 6.2 Namespace

New stable operations live under a versioned namespace:

```python
orca.host.v1
```

Existing unversioned read-only bindings remain available for backward compatibility. New plugins prefer the versioned snapshot API.

## 7. Plugin-scoped storage

```python
path = orca.host.v1.plugin_storage()
```

Required behavior:

- the host derives the active plugin identity from the call context;
- plugins cannot request another plugin's storage path;
- the host creates and canonicalizes the directory;
- the returned directory is writable during normal plugin lifecycle callbacks;
- the directory is outside OrcaSlicer configuration and secret locations;
- traversal and symlink escapes are rejected by the existing audit enforcement;
- uninstall behavior is explicit: retain data by default, with a separate user-confirmed removal action;
- the API never exposes `OrcaSlicer.conf` or cloud credential files.

Suggested layout:

```text
<data_dir>/orca_plugins_data/<canonical-plugin-id>/
```

`orca_filament` owns its SQLite schema and migrations inside this directory. The host does not interpret plugin database contents.

## 8. Immutable preset snapshot

```python
snapshot = orca.host.v1.presets.snapshot(
    config_keys={
        "print": ["layer_height", "outer_wall_speed"],
        "filament": ["nozzle_temperature"],
        "printer": ["nozzle_diameter"]
    }
)
```

The returned snapshot is a detached Python value graph, not borrowed pointers to native preset objects.

Required fields:

```text
revision                 opaque workspace revision
resources                print/filament/printer preset summaries
selected                  selected preset names by category
edited                    edited preset summaries by category
filament_slots            slot index, preset name, compatibility
active_printer            current printer preset identity
project_dirty             project dirty state
presets_dirty             aggregate preset dirty state
requested_values          serialized values for requested known keys
```

Preset summaries include:

```text
name
preset_type
visible
compatible
is_dirty
is_project_embedded
```

Sensitive keys are deny-listed from snapshots. In particular, printer-host credentials and API keys must never be returned through the generic preset snapshot API.

The revision changes whenever state relevant to a transaction changes. Its internal representation is not part of the public contract.

## 9. Preset transactions

### 9.1 Construction

```python
snapshot = orca.host.v1.presets.snapshot(...)
tx = orca.host.v1.presets.begin_transaction(
    expected_revision=snapshot.revision
)
```

Transactions are short-lived builders containing detached operation descriptions. They do not hold native `Tab`, `Preset`, or `DynamicPrintConfig` pointers.

### 9.2 Filament selection

```python
tx.select(
    preset_type="filament",
    name="Generic PLA",
    scope={"slot": 0}
)
```

The host validates:

- supported preset type;
- slot existence;
- preset existence and uniqueness;
- visibility and compatibility;
- printer and multi-material context;
- expected revision.

The backend must reuse the native UI selection path and its unsaved-changes handling. It must not write a preset filename or simulate mouse input.

### 9.3 Preset value changes

```python
tx.set_values(
    preset_type="print",
    scope={"edited": True},
    values={
        "layer_height": "0.16",
        "outer_wall_speed": "80"
    }
)
```

The host validates every key and serialized value against the native configuration definition before committing any mutation. Unknown, sensitive, read-only, or unsupported keys produce an `invalid` result.

V1 modifies only explicitly targeted edited presets. Creating, deleting, renaming, or silently saving presets is outside v1.

### 9.4 Commit

```python
result = tx.commit(interaction="require_user_confirmation")
```

Third-party plugins may use only `require_user_confirmation` in v1. Silent mutations are intentionally unavailable.

The host preview identifies:

- requesting plugin;
- affected preset and slot;
- old and proposed values;
- compatibility warnings;
- dirty presets and project implications;
- save, transfer, discard, or cancel choices provided by existing native flows.

All validation and mutation occur on the GUI thread. Expensive plugin work must remain outside the GUI thread.

### 9.5 Result

Conceptual result:

```python
{
    "status": "applied",
    "revision": "new-opaque-revision",
    "affected": [
        {"preset_type": "print", "name": "0.20 Standard"}
    ],
    "diagnostics": []
}
```

Statuses:

- `applied`: all requested host mutations were applied;
- `cancelled`: the user cancelled; no requested mutation remains applied;
- `conflict`: `expected_revision` is stale; no mutation was attempted;
- `invalid`: validation failed; diagnostics identify each rejected operation;
- `unsupported`: the host cannot satisfy the requested operation or scope;
- `error`: an unexpected native failure occurred and rollback completed.

### 9.6 Atomicity

Before presenting confirmation, the host validates all operations. Commit uses staged copies or a complete rollback snapshot so a failure cannot leave a partial set of requested value changes.

User-approved handling of pre-existing dirty state is part of the native interaction. Cancellation at any stage must not leave the plugin's requested selection or value mutations applied.

The host returns only after native state and visible GUI state agree.

## 10. Threading and lifecycle

- Host snapshot and transaction operations marshal to the GUI thread when called from an allowed plugin callback.
- A call originating from a slicing hook or shutdown state returns `unsupported` rather than risking deadlock.
- Plugin network and model calls never run on the GUI thread.
- Plugins own their worker threads and must stop them in `on_unload`.
- Plugins must not retain snapshot or transaction objects across unload.
- The host refuses new transactions after plugin cancellation begins.
- Long-running plugin operations use cancellation events and bounded network timeouts.

A broader declarative plugin permission system is desirable, but it should not be falsely emulated by a no-op `request_permissions` method. If permission metadata is not accepted in the first upstream PR, `orca_filament` must clearly disclose its LAN listener in its UI and use the host's existing plugin trust model until a separate permission proposal is approved.

## 11. AI Assist plugin design

### 11.1 UI

AI Assist uses a Pages capability with these finished views:

1. **Analyze presets**: shows the captured revision, included setting categories, and privacy notice.
2. **Describe a defect**: accepts a text description and requests targeted recommendations.
3. **Review changes**: lists each recommendation with checkbox, category, key, old value, proposed value, and explanation.
4. **Apply**: sends only checked rows through a transaction.
5. **Compatibility state**: explains automatic or manual mode based on feature discovery.

Provider keys and tokens use masked controls. Logs and error messages redact secrets.

### 11.2 Data minimization

Before a Gemini request, the plugin constructs an explicit allowlist of relevant setting keys. It excludes:

- `printhost_apikey`;
- tokens, passwords, URLs containing credentials, and cloud identifiers;
- unrelated user paths and project metadata.

The UI shows what categories will leave the machine and requires explicit opt-in before the first request. Camera frames require a separate opt-in.

### 11.3 Asynchronous requests

Gemini, webcam, Telegram, and Moonraker requests execute in cancellable worker tasks. UI messages marshal results back to the Pages bridge. Every request has bounded connect and total timeouts. Cancellation prevents late results from changing current UI state.

### 11.4 Response contract

Model responses use one explicit JSON schema. Parsing rejects malformed or unknown structures. Recommendation entries contain:

```text
preset_type
key
proposed_value
reason
confidence (optional display metadata only)
```

The plugin obtains trusted old values from the snapshot rather than accepting them from the model. The host remains the authority for type validation and application.

### 11.5 Monitoring release gate

Monitoring appears in the release only after all of these are true:

- prompt and parser use the same anomaly schema;
- monitoring checks that a print is active;
- interval and cost controls are enforced;
- camera source handling is unambiguous;
- Moonraker credentials are sent only to the configured Moonraker origin;
- direct webcam requests never inherit unrelated credentials;
- Telegram callbacks validate configured chat and authorized sender context;
- stop actions require explicit configuration and confirmation;
- shutdown cannot block indefinitely;
- parser, authorization, lifecycle, and HTTP tests pass.

If any gate remains unmet, the monitoring UI and claims are excluded from the first plugin release.

## 12. `orca_filament` plugin design

### 12.1 Load and compatibility

The plugin calls `api_info()` before optional host features. It does not call nonexistent methods during capability registration.

If `plugin_storage.v1` is absent, it may use an explicitly selected user data directory only if supported safely; otherwise it disables persistent inventory with an actionable message. It never writes into OrcaSlicer configuration directories by guessing paths.

If preset selection is absent, a scan displays the target preset and slot for manual selection.

### 12.2 Scan workflow

1. A phone authenticates with the configured PIN.
2. The phone scans or uploads a QR code.
3. The plugin validates and looks up the spool identifier.
4. The request is persisted as pending before host mutation.
5. The Pages UI obtains a fresh preset snapshot.
6. The plugin begins a transaction with that revision.
7. The transaction selects the mapped filament preset for the target slot.
8. The host shows native dirty-state and compatibility UI.
9. On `applied`, the plugin marks the request complete.
10. On `cancelled`, `conflict`, or `invalid`, the pending request remains visible with a retry or manual action.

### 12.3 Local data

SQLite receives an explicit schema version and migrations. Database writes use transactions. Pending scans survive restart. Backup/restore is not advertised until implemented and tested.

Sessions expire, can be revoked, and are cleared when security settings change. Image decoding checks dimensions before expensive processing where libraries allow it and treats decompression-bomb warnings as rejection.

The LAN server uses a bounded request body, bounded concurrency, rate limits for authentication and decode endpoints, safe shutdown, and an explicit trusted-LAN warning. TLS is not claimed; users needing encryption terminate TLS through a documented trusted reverse proxy only after that deployment path is tested.

## 13. Backward compatibility

- Existing `.3mf` and preset formats do not change.
- Existing plugin APIs remain available.
- The new API is additive.
- Plugins never serialize or rewrite `.3mf` archives directly.
- Native OrcaSlicer selection and dirty-state paths preserve project-embedded preset precedence.
- A host lacking the extension produces reduced functionality, not plugin load failure.
- A host with a newer incompatible API does not receive transactions unless the required feature identifiers remain present.

## 14. Security model

The host API follows least authority:

- no native pointers or arbitrary config access;
- no credential-bearing preset keys;
- no arbitrary file paths;
- no cross-plugin storage selection;
- no silent third-party preset mutation in v1;
- no stale transaction commits;
- no plugin-controlled bypass of dirty-state dialogs;
- no direct project or preset file writes.

Plugin-specific security remains the plugin's responsibility. Host transaction safety does not make arbitrary Python, inbound network listeners, dependency installation, or model-provider requests trusted.

## 15. Testing strategy

### 15.1 C++ host tests

Add targeted tests under `tests/slic3rutils` for:

- API discovery and exact feature identifiers;
- behavior when a feature is absent;
- plugin storage identity, creation, isolation, traversal, and denied configuration access;
- detached snapshot consistency and sensitive-key exclusion;
- revision changes and stale revision conflicts;
- nonexistent, incompatible, and ambiguous presets;
- single- and multi-filament slot selection;
- invalid config key and invalid serialized value;
- multiple changes with all-or-nothing rollback;
- dirty preset save, transfer, discard, and cancel paths where testable;
- transaction calls during shutdown and forbidden contexts;
- plugin unload with no retained host references.

### 15.2 AI Assist tests

- preset allowlist and secret exclusion;
- request schema and parser agreement;
- malformed, partial, and adversarial model responses;
- selected-row filtering;
- stale revision conflict and refresh;
- manual fallback mode;
- cancellation and late-response suppression;
- camera-origin credential isolation;
- Telegram callback authorization;
- monitor active-print gating and shutdown.

HTTP tests use local fakes; automated tests do not require live Gemini, Telegram, or Moonraker services.

### 15.3 `orca_filament` tests

- SQLite creation and migrations;
- collision retry and concurrent operations;
- durable pending scans;
- QR generation and decode round trip;
- PIN throttling, session expiry, and revocation;
- malformed and oversized image rejection;
- bind conflict and server shutdown;
- slot selection outcomes and manual fallback;
- restart during a pending scan.

### 15.4 Manual cross-platform matrix

Verify Windows, macOS, and Linux with:

- a single-extruder printer;
- a multi-extruder or multi-slot setup;
- normal and project-embedded presets;
- dirty print, filament, and printer presets;
- a `.3mf` reopened after user-approved changes;
- plugin install, disable, enable, update, and uninstall;
- official OrcaSlicer without the patch;
- patched stable build;
- upstream `main` build.

## 16. Distribution and compatibility manifest

Repository outputs use separate versioning:

```text
patches/
  v2.5.0/
    preset-workspace-api.patch
  compatibility.json

plugins/
  ai_assist/
  orca_filament/

release artifacts/
  patched OrcaSlicer packages
  independently installable plugin packages
```

`compatibility.json` records:

```text
OrcaSlicer tag and source commit
patch revision
host API version and features
compatible plugin version ranges
source-tree precondition or checksum
build/test status by platform
```

CI applies the patch to a clean supported tag before building. Failure to apply is a release blocker. CI also tests the API branch against current upstream `main` to expose conflicts early.

Installed binaries are never modified in place.

## 17. Upstream pull request strategy

The upstream PR is prepared from current upstream `main`, not from the AI fork or stable release patch branch. It contains a reviewable commit sequence:

1. API discovery and feature tests;
2. plugin-scoped storage and isolation tests;
3. immutable preset snapshot and revision tests;
4. generic preset transaction, selection, validation, and rollback tests;
5. Python documentation/examples.

The PR description explains two motivating plugin classes without requiring maintainers to accept either plugin:

- an AI settings assistant that proposes user-reviewed preset changes;
- a filament inventory/QR plugin that requests native slot selection.

The request emphasizes that the API enables general third-party plugins, preserves native confirmation and compatibility behavior, and does not add provider-specific code.

## 18. Migration sequence

1. Establish a clean branch/worktree at official v2.5.0.
2. Implement host contract tests before host implementation.
3. Implement discovery, storage, snapshot, and transactions.
4. Produce the stable source patch and verify clean application.
5. Build AI Assist as a plugin using manual fallback first, then transaction support.
6. Remove native AI wiring only after plugin parity is verified.
7. Make `orca_filament` loadable, persistent, and API-aware.
8. Run C++, Python, integration, security, and cross-platform checks.
9. Produce patched stable packages and compatibility metadata.
10. Rebase the generic host commits onto current upstream `main`.
11. Run upstream-targeted tests and create the official pull request.

## 19. Release gates

No component is called final until:

- its advertised features are implemented and tested;
- unfinished controls and documentation claims are removed;
- secrets are excluded from logs and external payloads;
- GUI operations remain responsive and cancellable;
- host mutations are atomic and user-confirmed;
- official-host fallback behavior is verified;
- plugin unload leaves no workers, listeners, or host references alive;
- source patch reproducibility and compatibility metadata are verified;
- the upstream PR contains only generic API code, tests, and documentation.

## 20. Open implementation decisions

These decisions are resolved during implementation without changing the product boundary:

- exact C++ revision-token representation;
- whether v1 exposes Python transaction builder objects or one immutable operation-list call;
- the native UI component used for multi-change preview;
- whether plugin permission metadata is a separate upstream PR;
- which platform packages can be produced by available CI runners.

Any decision that weakens user confirmation, atomicity, credential exclusion, or fallback compatibility requires a design revision and user approval.

## 21. Revision 2026-11-13

Official OrcaSlicer v2.5.0 (`40ecc66f78ed72dc368e0c1ebe40d77f00cc8a19`) already ships a preset transaction API (`orca.host.preset.*.begin_update/set/commit`, #16518), per-plugin storage (`orca.host.plugin.storage()`) and a plugin permission system. The fork's own host API is dropped. The user approved these decisions:

1. Both plugins use only the upstream v2.5.0 API. `orca.host.api_info` and `orca.host.v1` are not used.
2. AI Assist applies one preset category (process, filament or printer) per user action, as one upstream transaction after a native Yes/No confirmation. "Atomic" in section 19 now means atomic per category action. There is no cross-category atomicity.
3. Releases contain plugin packages and the compatibility manifest only. There is no patched OrcaSlicer build and no fork installer.
4. There is no upstream pull request.

For the release, sections 4.1, 9.4–9.6, 16 and 17 are replaced by `docs/superpowers/plans/2026-11-13-plugins-on-official-v2.5.0.md`.
