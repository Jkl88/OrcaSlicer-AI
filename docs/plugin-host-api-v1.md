# OrcaSlicer Plugin Host API v1

A versioned, provider-neutral API that lets Python plugins read the preset workspace and request preset
changes that the user confirms in a native dialog. Everything lives under `orca.host.v1`; existing
unversioned `orca.host` bindings keep working.

## Feature discovery

```python
info = orca.host.api_info()
# {"api_version": "1.0", "host_version": "<SLIC3R_VERSION>",
#  "features": ["plugin_storage.v1", "preset_snapshot.v1", "preset_transaction.v1",
#               "preset_select.v1", "preset_values.v1"]}
```

Check the feature you need, never the version string. Official OrcaSlicer builds have no `api_info`, so
wrap the call and fall back to a manual mode:

```python
try:
    features = set(orca.host.api_info()["features"])
except Exception:
    features = set()
```

## Plugin storage — `plugin_storage.v1`

```python
path = orca.host.v1.plugin_storage()   # "<data_dir>/orca_plugins_data/<plugin-key>"
```

- The directory is created on demand and is private to the calling plugin.
- The plugin is identified from the active plugin context, so call it from `on_load`, `on_message` or another
  host callback on the GUI thread, and cache the path. From a thread you started yourself it raises
  `RuntimeError`.
- Uninstalling a plugin keeps its data unless the user ticks "Remove plugin data".

## Snapshot — `preset_snapshot.v1`

```python
snap = orca.host.v1.presets.snapshot(config_keys={"print": ["layer_height"], "filament": ["nozzle_temperature"]})
```

Returns a detached dict:

| key | content |
|---|---|
| `revision` | hash of the workspace; changes whenever selection, edited values or dirty state change |
| `resources` | `{"print"\|"filament"\|"printer": [{"name", "preset_type", "visible", "compatible", "is_dirty", "is_project_embedded"}]}` |
| `selected` | `{"print": name, "filament": name, "printer": name}` |
| `edited` | the same summary dict as in `resources` for the edited preset of each type |
| `filament_slots` | `[{"slot": 0, "preset_name": "...", "compatible": true}, ...]`, zero-based |
| `active_printer` | summary of the edited printer preset |
| `project_dirty`, `presets_dirty` | booleans |
| `requested_values` | `{type: {key: serialized_value}}` for the requested keys of the edited presets |
| `diagnostics` | e.g. `{"code": "sensitive_key", "key": ...}` for keys that were refused |

Values are OrcaSlicer's serialized strings (`"0.2"`, `"45%"`, `"220,220"`). Unknown keys are skipped.
Credentials (API keys, tokens, passwords, user names, `print_host`, certificates) are never returned.

## Commit — `preset_transaction.v1`, `preset_values.v1`, `preset_select.v1`

```python
result = orca.host.v1.presets.commit({
    "expected_revision": snap["revision"],
    "interaction": "require_user_confirmation",
    "operations": [
        {"op": "set_values", "preset_type": "print", "scope": {"edited": True},
         "values": {"layer_height": "0.16"}},
        {"op": "select", "preset_type": "filament", "name": "Generic PETG", "scope": {"slot": 1}},
    ],
})
```

The host:

1. returns `conflict` if `expected_revision` differs from the current revision;
2. validates every operation — key exists, value deserializes, key is not a credential, slot exists, filament
   preset exists and is compatible with the printer;
3. shows one dialog listing each change (preset, setting or slot, current value, new value);
4. applies value edits through the settings tabs and slot selections through the same path as the sidebar
   combo box, then returns the new revision.

If the user cancels, a selection is refused (for example the user cancels the unsaved-changes prompt when
the Filament tab follows slot 1), or anything throws, everything already applied is restored. Presets are
never saved; edited presets show as modified.

Rules:

- `select` supports `preset_type: "filament"` with scope `{"slot": n}` (zero-based); use internal preset
  names from `resources`, not display aliases.
- `set_values` supports scope `{"edited": true}`; values must be strings; a key may appear once per
  transaction.
- A transaction cannot combine a filament `select` with filament `set_values`.
- `interaction` defaults to `"require_user_confirmation"`, the only mode in v1.
- If nothing would change, the result is `applied` with an empty `affected` list and no dialog.

`commit` blocks until the user answers. Pages keep receiving messages while the dialog is open, so guard
your `on_message` against starting a second commit.

### Result

```python
{"status": "applied", "revision": "...", "affected": [...], "diagnostics": [], "message": "..."}
```

| status | meaning |
|---|---|
| `applied` | confirmed and applied; `affected` lists each change |
| `cancelled` | the user declined, or cancelled the unsaved-changes prompt |
| `conflict` | the workspace changed; take a fresh snapshot, rebuild, ask the user again — never retry silently |
| `invalid` | malformed request or rejected value; see `diagnostics` (`code`, `operation_index`, `field`, `message`) |
| `invalid_slot`, `not_found`, `incompatible` | the filament selection cannot be made |
| `unsupported` | unsupported `interaction`, or the GUI is not available (for example during shutdown) |
| `error` | unexpected failure after confirmation; changes were rolled back |

`commit` reports problems through `status` and does not raise for bad requests.

## Example

`sandboxes/orca_preset_transaction_plugin_any.py` is a minimal Pages plugin that detects the features, reads a
snapshot and commits a change. The AI Assist and Filament QR plugins in `plugins/` are complete examples.
