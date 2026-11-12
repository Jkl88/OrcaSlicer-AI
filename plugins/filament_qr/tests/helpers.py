import importlib.util
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[1] / 'orca_filament_qr.py'


def load_plugin(name='orca_filament_qr_under_test'):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fq = load_plugin()


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='fq-test-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


FEATURES = ['plugin_storage.v1', 'preset_snapshot.v1', 'preset_transaction.v1', 'preset_select.v1',
            'preset_values.v1']


class FakePresets:
    """Models orca.host.v1.presets with the host's revision and selection semantics."""

    def __init__(self, slots, profiles, printer='Printer A', answer='confirm'):
        self.slots = list(slots)
        self.profiles = dict(profiles)  # name -> compatible
        self.printer = printer
        self.answer = answer
        self.revision_counter = 0
        self.commits = []
        self.forced_result = None

    def revision(self):
        return f'rev-{self.revision_counter}'

    def snapshot(self, config_keys=None):
        return {
            'revision': self.revision(),
            'resources': {'filament': [{'name': n, 'visible': True, 'compatible': c} for n, c in self.profiles.items()]},
            'filament_slots': [{'slot': i, 'preset_name': n, 'compatible': True} for i, n in enumerate(self.slots)],
            'active_printer': {'name': self.printer},
            'presets_dirty': False,
        }

    def commit(self, request):
        self.commits.append(request)
        if self.forced_result is not None:
            return self.forced_result
        if request['expected_revision'] != self.revision():
            return {'status': 'conflict', 'revision': self.revision(), 'affected': [], 'diagnostics': []}
        if self.answer != 'confirm':
            return {'status': 'cancelled', 'revision': self.revision(), 'affected': [], 'diagnostics': []}
        affected = []
        for op in request['operations']:
            slot = op['scope']['slot']
            affected.append({'op': 'select', 'slot': slot, 'old_name': self.slots[slot], 'name': op['name']})
            self.slots[slot] = op['name']
        self.revision_counter += 1
        return {'status': 'applied', 'revision': self.revision(), 'affected': affected, 'diagnostics': []}


def automatic_host(storage_dir, slots=('Generic PLA',), profiles=None):
    presets = FakePresets(slots, profiles or {'Generic PLA': True, 'Generic PETG': True, 'Other ABS': False})
    v1 = types.SimpleNamespace(plugin_storage=lambda: str(storage_dir), presets=presets)
    return types.SimpleNamespace(api_info=lambda: {'api_version': '1.0', 'features': FEATURES}, v1=v1)


class _LegacyPreset:
    def __init__(self, name, compatible=True):
        self.name, self.is_visible, self.is_compatible, self.is_dirty = name, True, compatible, False


class _LegacyFilaments:
    def __init__(self, names):
        self.items = [_LegacyPreset(n) for n in names]

    def size(self):
        return len(self.items)

    def preset(self, i):
        return self.items[i]

    def edited_preset(self):
        return self.items[0]


def legacy_host(slots=('Generic PLA',), profiles=('Generic PLA', 'Generic PETG')):
    bundle = types.SimpleNamespace(
        filaments=_LegacyFilaments(profiles),
        printers=types.SimpleNamespace(selected_preset_name=lambda: 'Printer A'),
        current_filament_preset_names=lambda: list(state['slots']))
    state = {'slots': list(slots)}
    host = types.SimpleNamespace(preset_bundle=lambda: bundle, state=state)
    return host


def install_fake_orca(host):
    """Build a minimal `orca` module that mirrors the registration surface OrcaSlicer exposes."""
    fake = types.ModuleType('orca')
    registered = []

    class PagesPluginCapabilityBase:
        def __init__(self):
            self.posted = []

        def post_message(self, data):
            self.posted.append(data)

    fake.pages = types.SimpleNamespace(PagesPluginCapabilityBase=PagesPluginCapabilityBase)
    fake.base = type('base', (), {})
    fake.plugin = lambda cls: cls
    fake.register_capability = registered.append
    fake.host = host
    fake.registered = registered
    sys.modules['orca'] = fake
    return fake
