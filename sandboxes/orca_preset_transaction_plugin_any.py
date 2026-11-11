#!/usr/bin/env python3
"""
Example OrcaSlicer plugin demonstrating preset transaction API v1.

This plugin shows how to:
- Detect API features
- Capture preset snapshots
- Build transaction requests
- Handle all result statuses
- Convert user-facing slot numbers
- Degrade gracefully on unpatched hosts

Usage:
    1. Install as OrcaSlicer plugin
    2. Open in OrcaSlicer (plugin will appear in Pages panel)
    3. Click "Analyze Presets" to get AI suggestions
    4. Review and apply suggested changes with one click
"""

import orca
from typing import Dict, List, Any, Optional


class PresetAssistantPlugin:
    """AI-assisted preset optimization plugin."""

    def __init__(self):
        self.has_transactions = False
        self.has_snapshots = False
        self.has_storage = False
        self._detect_features()

    def _detect_features(self):
        """Detect which host API features are available."""
        try:
            info = orca.host.api_info()
            features = info.get("features", [])

            self.has_transactions = "preset_transaction.v1" in features
            self.has_snapshots = "preset_snapshot.v1" in features
            self.has_storage = "plugin_storage.v1" in features

            print(f"✓ Host API v{info['api_version']}")
            print(f"  Transactions: {'yes' if self.has_transactions else 'no'}")
            print(f"  Snapshots: {'yes' if self.has_snapshots else 'no'}")
            print(f"  Storage: {'yes' if self.has_storage else 'no'}")
        except (AttributeError, KeyError):
            print("⚠ No host API detected - using manual mode")

    def get_storage_path(self) -> Optional[str]:
        """Get plugin-private storage directory."""
        if not self.has_storage:
            return None

        try:
            return orca.host.v1.plugin_storage()
        except Exception as e:
            print(f"✗ Storage access failed: {e}")
            return None

    def capture_snapshot(self, keys: Optional[Dict[str, List[str]]] = None) -> Optional[Dict[str, Any]]:
        """Capture current preset workspace state."""
        if not self.has_snapshots:
            print("⚠ Snapshots not available")
            return None

        # Default: capture common tuning parameters
        if keys is None:
            keys = {
                "print": [
                    "layer_height",
                    "first_layer_height",
                    "wall_loops",
                    "top_shell_layers",
                    "bottom_shell_layers",
                    "sparse_infill_density",
                    "sparse_infill_pattern"
                ],
                "filament": [
                    "filament_type",
                    "filament_colour",
                    "temperature",
                    "bed_temperature",
                    "filament_flow_ratio"
                ],
                "printer": [
                    "printer_model",
                    "nozzle_diameter"
                ]
            }

        try:
            snapshot = orca.host.v1.presets.snapshot(config_keys=keys)
            print(f"✓ Captured snapshot (revision: {snapshot['revision'][:8]}...)")
            return snapshot
        except Exception as e:
            print(f"✗ Snapshot failed: {e}")
            return None

    def analyze_presets(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Analyze current presets and suggest improvements.

        In a real plugin, this would:
        1. Send snapshot to LLM (Gemini, GPT, Claude)
        2. Ask for optimization suggestions
        3. Parse recommendations into operations

        This example returns hardcoded suggestions for demonstration.
        """
        suggestions = []

        # Example: Check if layer height seems suboptimal
        print_config = snapshot["resources"].get("print", {})
        layer_height = float(print_config.get("layer_height", "0.2"))

        if layer_height > 0.24:
            suggestions.append({
                "op": "set_values",
                "preset_type": "print",
                "scope": {"edited": True},
                "values": {"layer_height": "0.20"},
                "reason": "Reduce layer height for better surface quality"
            })

        # Example: Check first layer height ratio
        first_layer = float(print_config.get("first_layer_height", "0.3"))
        if first_layer / layer_height < 1.2:
            suggestions.append({
                "op": "set_values",
                "preset_type": "print",
                "scope": {"edited": True},
                "values": {"first_layer_height": str(layer_height * 1.5)},
                "reason": "Increase first layer height for better bed adhesion"
            })

        # Example: Suggest infill optimization
        infill = int(print_config.get("sparse_infill_density", "15"))
        if infill > 25:
            suggestions.append({
                "op": "set_values",
                "preset_type": "print",
                "scope": {"edited": True},
                "values": {"sparse_infill_density": "20"},
                "reason": "Reduce infill to save material and time"
            })

        return suggestions

    def apply_suggestions(self, snapshot: Dict[str, Any], suggestions: List[Dict[str, Any]]) -> bool:
        """
        Apply suggested changes through transaction API.

        Returns True if applied successfully, False otherwise.
        """
        if not self.has_transactions:
            self._show_manual_instructions(suggestions)
            return False

        if not suggestions:
            print("✓ No changes needed")
            return True

        # Build transaction request
        operations = [
            {k: v for k, v in s.items() if k != "reason"}
            for s in suggestions
        ]

        request = {
            "expected_revision": snapshot["revision"],
            "operations": operations
        }

        print(f"\nProposed changes:")
        for i, s in enumerate(suggestions, 1):
            print(f"  {i}. {s['reason']}")
            if s["op"] == "set_values":
                for key, value in s["values"].items():
                    old_val = snapshot["resources"][s["preset_type"]].get(key, "?")
                    print(f"     {key}: {old_val} → {value}")

        # Commit with user confirmation
        try:
            result = orca.host.v1.presets.commit(request)

            if result["status"] == "applied":
                print(f"\n✓ Changes applied successfully")
                print(f"  New revision: {result['revision'][:8]}...")
                return True

            elif result["status"] == "conflict":
                print(f"\n⚠ Workspace changed during analysis")
                print(f"  Current revision: {result['revision'][:8]}...")
                print(f"  Please run analysis again")
                return False

            elif result["status"] == "cancelled":
                print(f"\n✗ User cancelled changes")
                return False

            elif result["status"] == "invalid":
                print(f"\n✗ Validation failed:")
                for diag in result.get("diagnostics", []):
                    print(f"  {diag['code']}: {diag['message']}")
                return False

            else:
                print(f"\n✗ Error: {result.get('message', 'Unknown error')}")
                return False

        except Exception as e:
            print(f"\n✗ Exception during commit: {e}")
            return False

    def _show_manual_instructions(self, suggestions: List[Dict[str, Any]]):
        """Show manual instructions when transaction API is unavailable."""
        print("\n⚠ Automatic apply not available - manual steps:")

        for i, s in enumerate(suggestions, 1):
            print(f"\n{i}. {s['reason']}")

            if s["op"] == "set_values":
                preset_type = s["preset_type"]
                print(f"   → Open {preset_type.capitalize()} settings")

                for key, value in s["values"].items():
                    key_display = key.replace("_", " ").title()
                    print(f"   → Set '{key_display}' to '{value}'")

            elif s["op"] == "select":
                slot = s["scope"]["slot"]
                user_slot = slot + 1  # Convert to 1-based
                print(f"   → Select '{s['name']}' for Slot {user_slot}")

    def select_filament_for_slot(self, snapshot: Dict[str, Any], preset_name: str, user_slot: int) -> bool:
        """
        Select a filament preset for a specific slot.

        Args:
            snapshot: Current workspace snapshot
            preset_name: Internal filament preset name
            user_slot: User-facing slot number (1-based)

        Returns:
            True if applied successfully, False otherwise.
        """
        if not self.has_transactions:
            print(f"⚠ Please manually select '{preset_name}' for Slot {user_slot}")
            return False

        # Convert user-facing slot (1-based) to API slot (0-based)
        api_slot = user_slot - 1

        # Validate slot number
        num_slots = snapshot.get("filament_slots", 0)
        if api_slot < 0 or api_slot >= num_slots:
            print(f"✗ Invalid slot: {user_slot} (valid: 1-{num_slots})")
            return False

        request = {
            "expected_revision": snapshot["revision"],
            "operations": [{
                "op": "select",
                "preset_type": "filament",
                "name": preset_name,
                "scope": {"slot": api_slot}
            }]
        }

        print(f"\nSelecting '{preset_name}' for Slot {user_slot}...")

        try:
            result = orca.host.v1.presets.commit(request)

            if result["status"] == "applied":
                print(f"✓ Filament selected successfully")
                return True
            elif result["status"] == "conflict":
                print(f"⚠ Workspace changed, please retry")
                return False
            elif result["status"] == "cancelled":
                print(f"✗ User cancelled selection")
                return False
            else:
                print(f"✗ Selection failed: {result.get('message', 'Unknown')}")
                return False

        except Exception as e:
            print(f"✗ Exception: {e}")
            return False

    def run(self):
        """Main plugin workflow."""
        print("\n" + "="*60)
        print("OrcaSlicer Preset Assistant Plugin")
        print("="*60)

        # Step 1: Capture current state
        print("\n1. Capturing preset snapshot...")
        snapshot = self.capture_snapshot()
        if not snapshot:
            print("✗ Cannot proceed without snapshot")
            return

        # Show current state
        print(f"\nCurrent configuration:")
        print(f"  Print: {snapshot['selected']['print']}")
        print(f"  Printer: {snapshot['selected']['printer']}")
        print(f"  Filaments: {len(snapshot['selected']['filament'])} slots")
        for i, fil in enumerate(snapshot['selected']['filament'], 1):
            print(f"    Slot {i}: {fil}")

        # Step 2: Analyze and suggest improvements
        print(f"\n2. Analyzing presets...")
        suggestions = self.analyze_presets(snapshot)

        if not suggestions:
            print("✓ Current settings look optimal!")
            return

        # Step 3: Apply suggestions
        print(f"\n3. Applying {len(suggestions)} suggested changes...")
        success = self.apply_suggestions(snapshot, suggestions)

        if success:
            print("\n" + "="*60)
            print("✓ Preset optimization complete!")
            print("="*60)


# Plugin entry point
if __name__ == "__main__":
    plugin = PresetAssistantPlugin()
    plugin.run()
