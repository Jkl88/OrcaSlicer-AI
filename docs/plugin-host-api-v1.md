# OrcaSlicer Plugin Host API v1

Provider-neutral, versioned API for plugin-initiated preset changes with user confirmation.

## Feature Discovery

```python
import orca

info = orca.host.api_info()
# Returns: {
#   "api_version": "1.0",
#   "host_version": "2.5.0+plugin-api",
#   "features": [
#     "plugin_storage.v1",
#     "preset_snapshot.v1", 
#     "preset_transaction.v1",
#     "preset_select.v1",
#     "preset_values.v1"
#   ]
# }

# Check for specific feature before use
if "preset_transaction.v1" in info["features"]:
    # Use v1.presets.commit()
    pass
```

## Plugin-Private Storage

Each plugin gets an isolated directory for persistent state.

```python
storage_path = orca.host.v1.plugin_storage()
# Returns: "/path/to/data_dir/orca_plugins_data/<plugin-key>/"

# Storage is:
# - Private to this plugin (other plugins cannot read it)
# - Persistent across restarts
# - Automatically created on first access
# - Retained when plugin is uninstalled (unless user confirms deletion)
```

**Security**: Storage path is canonicalized and validated to prevent directory traversal.

## Preset Workspace Snapshot

Captures immutable snapshot of current preset state.

```python
snapshot = orca.host.v1.presets.snapshot(config_keys={
    "print": ["layer_height", "first_layer_height"],
    "filament": ["filament_colour", "temperature"],
    "printer": ["printer_model"]
})

# Returns: {
#   "revision": "abc123...",  # Changes when workspace state changes
#   "selected": {
#     "print": "0.20mm Standard",
#     "filament": ["Generic PLA", "Generic PETG"],
#     "printer": "Bambu Lab X1 Carbon"
#   },
#   "edited": {
#     "print": false,
#     "filament": false,
#     "printer": false
#   },
#   "filament_slots": 2,
#   "active_printer": "Bambu Lab X1 Carbon",
#   "project_dirty": false,
#   "presets_dirty": false,
#   "resources": {
#     "print": {
#       "layer_height": "0.2",
#       "first_layer_height": "0.3"
#     },
#     "filament": [
#       {
#         "filament_colour": "#FF0000",
#         "temperature": "210"
#       },
#       {
#         "filament_colour": "#0000FF", 
#         "temperature": "210"
#       }
#     ],
#     "printer": {
#       "printer_model": "Bambu Lab X1 Carbon"
#     }
#   }
# }
```

**Important notes:**
- `revision` is deterministic hash of workspace state
- Sensitive keys (API keys, tokens, passwords) are **always omitted**
- Unknown keys are silently omitted
- Filament resources are returned as array matching slot order
- **Zero-based internally**: `filament_slots: 2` means slots 0 and 1

## Preset Transaction Commit

Apply preset changes with user confirmation.

```python
request = {
    "expected_revision": snapshot["revision"],
    "operations": [
        {
            "op": "set_values",
            "preset_type": "print",
            "scope": {"edited": true},
            "values": {
                "layer_height": "0.16",
                "first_layer_height": "0.24"
            }
        },
        {
            "op": "select",
            "preset_type": "filament",
            "name": "Generic ABS",
            "scope": {"slot": 0}  # Zero-based slot index
        }
    ]
}

result = orca.host.v1.presets.commit(request)

# Possible statuses:
# - "applied": Changes were confirmed and applied
# - "cancelled": User cancelled the confirmation dialog
# - "conflict": expected_revision is stale (snapshot changed since request was built)
# - "invalid": Validation failed (unknown key, sensitive key, malformed value)
# - "unsupported": Feature not available or unsupported interaction mode
# - "error": Exception during apply (changes were rolled back)

if result["status"] == "applied":
    print(f"Applied successfully, new revision: {result['revision']}")
elif result["status"] == "conflict":
    # Workspace changed, capture fresh snapshot and rebuild request
    new_snapshot = orca.host.v1.presets.snapshot()
    # ... rebuild request with new_snapshot["revision"]
elif result["status"] == "invalid":
    # Check diagnostics for details
    for diag in result["diagnostics"]:
        print(f"Error: {diag['code']} - {diag['message']}")
```

## Operation Types

### `set_values` - Modify Edited Preset

Changes configuration values in the edited preset.

```python
{
    "op": "set_values",
    "preset_type": "print" | "filament" | "printer",
    "scope": {"edited": true},  # v1 only supports edited presets
    "values": {
        "key1": "serialized_value1",
        "key2": "serialized_value2"
    }
}
```

**Validation:**
- All keys must exist in preset config
- Sensitive keys (containing "key", "password", "token", "secret", "apikey") are rejected
- Values must deserialize correctly for their config option type
- All operations are validated **before** showing confirmation dialog

**Atomicity:**
- All values in transaction are validated before any are applied
- If any operation fails validation, **none** are applied
- If user cancels, **no changes** are made

### `select` - Change Filament Preset (v1)

Selects a different filament preset for a specific slot.

```python
{
    "op": "select",
    "preset_type": "filament",  # v1 only supports filament selection
    "name": "Generic ABS",       # Internal preset name (not display alias)
    "scope": {"slot": 0}         # Zero-based slot index
}
```

**Validation:**
- Slot must be within current `filament_slots` count
- Preset must exist and be compatible with current printer
- Uses internal preset name, not display alias

**Side effects:**
- Updates project dirty state
- Exports preset selections to app config
- Refreshes dynamic filament list in sidebar
- Triggers `on_filament_change` callback

## User-Facing Slot Numbers

**Critical:** Plugin API uses **zero-based** slot indexing internally, but users see **one-based** numbers.

```python
snapshot = orca.host.v1.presets.snapshot()
# snapshot["filament_slots"] = 2  means slots 0 and 1 exist

# To select for "Slot 2" shown to user:
request = {
    "operations": [{
        "op": "select",
        "preset_type": "filament", 
        "name": "Generic ABS",
        "scope": {"slot": 1}  # User's "Slot 2" = internal slot 1
    }]
}
```

## Confirmation Dialog

When `interaction: "require_user_confirmation"` (the only supported mode in v1):

1. **Before** confirmation, all operations are:
   - Parsed and validated
   - Staged (values deserialized, presets checked for existence/compatibility)
   
2. **During** confirmation, dialog shows:
   - Plugin name initiating the change
   - Category, preset, slot affected
   - Old value → New value for each change
   - Compatibility warnings
   
3. **After** confirmation:
   - All changes applied atomically
   - Config tabs marked dirty (but not auto-saved)
   - User must still save presets manually if desired

4. **On cancellation**:
   - No changes applied
   - All staged values discarded
   - Status returns `"cancelled"`

## Revision Conflicts

The `expected_revision` prevents race conditions:

```python
# Step 1: Capture snapshot
snapshot = orca.host.v1.presets.snapshot()

# Step 2: AI analyzes and builds request
request = {
    "expected_revision": snapshot["revision"],
    "operations": [...]
}

# Step 3: User changed presets in UI before commit
# Workspace revision changed from "abc123" to "def456"

result = orca.host.v1.presets.commit(request)
# Returns: {"status": "conflict", "revision": "def456"}

# Step 4: Capture fresh snapshot and rebuild
new_snapshot = orca.host.v1.presets.snapshot()
# Build new request with new_snapshot["revision"]
```

**Never retry a conflict automatically** - always capture fresh state and rebuild the request.

## Sensitive Key Protection

Keys containing these patterns are **always rejected** in `set_values`:

- `printhost_apikey`
- `*_key`, `*_password`, `*_token`, `*_secret`
- `*apikey*`
- `print_host`, `printhost_user`, `printhost_ssl_ca_file`

Sensitive keys are also **omitted from snapshots**, even when explicitly requested.

## Fallback for Unpatched Hosts

Official OrcaSlicer without plugin API patch:

```python
try:
    info = orca.host.api_info()
    if "preset_transaction.v1" in info["features"]:
        # Use automatic mode
        result = orca.host.v1.presets.commit(request)
    else:
        # Degrade to manual mode
        show_manual_instructions(changes)
except AttributeError:
    # No host.api_info() - very old host
    show_manual_instructions(changes)

def show_manual_instructions(changes):
    """Show user how to make changes manually."""
    for change in changes:
        print(f"1. Go to {change['preset_type']} settings")
        print(f"2. Set '{change['key']}' to '{change['new_value']}'")
```

## Best Practices

1. **Always check features before use**
   ```python
   info = orca.host.api_info()
   has_transactions = "preset_transaction.v1" in info["features"]
   ```

2. **Request only needed keys in snapshot**
   - Smaller snapshots = faster
   - Less data to send to LLM
   - Clearer revision tracking

3. **Handle all result statuses**
   - `applied`: Success
   - `cancelled`: User declined
   - `conflict`: Rebuild with fresh snapshot
   - `invalid`: Fix diagnostics and retry
   - `error`: Log and report to user

4. **Convert user-facing slot numbers**
   ```python
   user_slot = 2  # "Slot 2" in UI
   api_slot = user_slot - 1  # 1 for API
   ```

5. **Never save presets automatically**
   - v1 API only modifies edited presets
   - User must save via normal UI if desired
   - This prevents accidental overwrites

## Complete Example

```python
import orca

# Feature detection
info = orca.host.api_info()
if "preset_transaction.v1" not in info["features"]:
    print("Host does not support preset transactions")
    exit(1)

# Capture workspace state
snapshot = orca.host.v1.presets.snapshot(config_keys={
    "print": ["layer_height", "first_layer_height"],
    "filament": ["filament_colour", "temperature"]
})

print(f"Current layer height: {snapshot['resources']['print']['layer_height']}")
print(f"Current revision: {snapshot['revision']}")

# Build transaction request
request = {
    "expected_revision": snapshot["revision"],
    "operations": [
        {
            "op": "set_values",
            "preset_type": "print",
            "scope": {"edited": true},
            "values": {
                "layer_height": "0.16",
                "first_layer_height": "0.24"
            }
        },
        {
            "op": "select",
            "preset_type": "filament",
            "name": "Generic ABS",
            "scope": {"slot": 0}
        }
    ]
}

# Commit with user confirmation
result = orca.host.v1.presets.commit(request)

if result["status"] == "applied":
    print(f"✓ Changes applied successfully")
    print(f"New revision: {result['revision']}")
elif result["status"] == "conflict":
    print(f"⚠ Workspace changed, please retry")
    print(f"Current revision: {result['revision']}")
elif result["status"] == "cancelled":
    print(f"✗ User cancelled changes")
elif result["status"] == "invalid":
    print(f"✗ Validation failed:")
    for diag in result.get("diagnostics", []):
        print(f"  {diag['code']}: {diag['message']}")
else:
    print(f"✗ Error: {result.get('message', 'Unknown error')}")
```

## Version History

- **v1.0** (2026-11-11): Initial release
  - Plugin-private storage
  - Immutable preset snapshots
  - User-confirmed transactions
  - Filament slot selection
  - Edited preset value changes
