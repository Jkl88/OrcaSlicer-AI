# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "Preset Transaction Demo"
# description = "Minimal example of orca.host.v1: snapshot, then a user-confirmed preset change."
# author = "OrcaSlicer-AI"
# version = "1.0.0"
# ///
"""Preset Transaction Demo — the smallest useful orca.host.v1 plugin.

The page shows the current layer height and filament slots. "Thinner layers" asks OrcaSlicer to lower the
process layer height by 0.04 mm; "Slot 1 = slot 2" copies the second slot's filament into the first. Both go
through presets.commit(), so OrcaSlicer shows its confirmation dialog and nothing changes without consent.
"""
import orca

PAGE = r"""<!doctype html><meta charset="utf-8"><style>
body{font-family:system-ui;color:var(--orca-fg,#eee);background:var(--orca-bg,#222);margin:16px}
button{background:var(--orca-accent,#4a89e8);color:var(--orca-accent-fg,#fff);border:0;padding:8px 12px;border-radius:6px}
pre{white-space:pre-wrap}</style>
<h2>Preset transaction demo</h2><pre id="state">…</pre>
<button onclick="orca.postMessage({type:'thinner'})">Thinner layers</button>
<button onclick="orca.postMessage({type:'copy_slot'})">Slot 1 = slot 2</button>
<pre id="result"></pre>
<script>
orca.onMessage(d=>{if(d.type==='state')document.getElementById('state').textContent=d.text;
 if(d.type==='result')document.getElementById('result').textContent=JSON.stringify(d.result,null,2)});
orca.postMessage({type:'refresh'});
</script>"""

REQUIRED = {"preset_snapshot.v1", "preset_transaction.v1", "preset_values.v1", "preset_select.v1"}


def host_features():
    try:
        return set(orca.host.api_info()["features"])
    except Exception:
        return set()


class DemoPage(orca.pages.PagesPluginCapabilityBase):
    busy = False

    def get_name(self):
        return "Transaction demo"

    def get_ui(self):
        return PAGE

    def on_message(self, data):
        if not REQUIRED <= host_features():
            self.post_message({"type": "state", "text": "This OrcaSlicer build has no plugin API v1."})
            return
        if self.busy:  # commit() runs a modal dialog; the page can still send messages meanwhile
            return
        snap = orca.host.v1.presets.snapshot({"print": ["layer_height"]})
        layer = snap["requested_values"].get("print", {}).get("layer_height", "?")
        slots = [s["preset_name"] for s in snap["filament_slots"]]
        request = None
        if data.get("type") == "thinner" and layer != "?":
            new_value = max(0.04, round(float(layer) - 0.04, 2))
            request = {"op": "set_values", "preset_type": "print", "scope": {"edited": True},
                       "values": {"layer_height": str(new_value)}}
        elif data.get("type") == "copy_slot" and len(slots) > 1:
            request = {"op": "select", "preset_type": "filament", "name": slots[1], "scope": {"slot": 0}}
        if request is not None:
            self.busy = True
            try:
                result = orca.host.v1.presets.commit({"expected_revision": snap["revision"],
                                                      "interaction": "require_user_confirmation",
                                                      "operations": [request]})
            finally:
                self.busy = False
            self.post_message({"type": "result", "result": result})
            snap = orca.host.v1.presets.snapshot({"print": ["layer_height"]})
            layer = snap["requested_values"].get("print", {}).get("layer_height", "?")
            slots = [s["preset_name"] for s in snap["filament_slots"]]
        text = f"Layer height: {layer}\n" + "\n".join(f"Slot {i + 1}: {n}" for i, n in enumerate(slots))
        self.post_message({"type": "state", "text": text})


@orca.plugin
class DemoPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(DemoPage)
