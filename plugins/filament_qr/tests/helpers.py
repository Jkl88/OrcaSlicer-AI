import importlib.util
import json
import shutil
import sys
import tempfile
import threading
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


class _Preset:
    def __init__(self, name, compatible=True, config=None, file=''):
        self.name, self.is_visible, self.is_compatible, self.is_dirty = name, True, compatible, False
        self.config = config or {}
        self.file = file

    def config_value(self, key):
        return self.config.get(key)


class _Filaments:
    def __init__(self, profiles):
        self.items = [_Preset(n, c) for n, c in profiles.items()]

    def size(self):
        return len(self.items)

    def preset(self, i):
        return self.items[i]

    def edited_preset(self):
        return self.items[0]

    def find_preset(self, name):
        return next((p for p in self.items if p.name == name), None)

    def preset_names(self):
        return [p.name for p in self.items]


def _bundle(state, profiles):
    def full_config_value(key):
        return ';'.join(state['colours']) if key == 'filament_colour' else None

    return types.SimpleNamespace(
        filaments=_Filaments(profiles),
        printers=types.SimpleNamespace(selected_preset_name=lambda: 'Printer A'),
        current_filament_preset_names=lambda: list(state['slots']),
        full_config_value=full_config_value)


class UpstreamHost:
    """OrcaSlicer 2.5.0 surface. begin_update/commit raise when called on the thread that built the host."""

    def __init__(self, storage_dir, slots, profiles, colour_api=False, bundles=True, folder='default'):
        self.ui_thread = threading.current_thread()
        self.data_root = Path(storage_dir) / 'data'
        self.folder = folder
        self.bundle_profiles = {}   # canonical name -> preset JSON
        self.user_profiles = set()  # names of presets stored in the active user folder
        self.reloads, self.reload_error = [], None
        (self.data_root / 'user' / folder).mkdir(parents=True, exist_ok=True)
        self.state = {'slots': list(slots), 'colours': ['#FFFFFF'] * len(slots)}
        self.profiles = dict(profiles)
        self.profile_configs = {}
        self.answer = 'yes'
        self.dialogs, self.begins, self.commits, self.violations = [], [], [], []
        self.on_begin = self.begin_error = self.commit_error = self.target_override = None
        storage = self.data_root / 'orca_plugins' / 'plugin_data' / 'orca_filament_qr'
        self.plugin = types.SimpleNamespace(storage=lambda: str(storage))
        self.ui = types.SimpleNamespace(message=self.message)
        self.preset = types.SimpleNamespace(filament=types.SimpleNamespace(begin_update=self.begin_update))
        if bundles:
            self.preset.bundles = types.SimpleNamespace(reload_local=self.reload_local, list_ids=self.list_ids)
        self.colour_error = None
        if colour_api:
            self.preset.filament.set_slot_colour = self.set_slot_colour

    def set_slot_colour(self, slot_number, colour):
        self.off_ui('set_slot_colour')
        if self.colour_error:
            raise self.colour_error
        self.state['colours'][slot_number - 1] = colour

    @property
    def slots(self):
        return self.state['slots']

    def local_dir(self):
        return self.data_root / 'user' / self.folder / '_local'

    def list_ids(self):
        root = self.local_dir()
        return sorted(d.name for d in root.iterdir() if (d / 'bundle_metadata.json').is_file()) if root.is_dir() else []

    def reload_local(self, bundle_id):
        """Mirror PresetBundle::reload_local_bundle: replace every preset of the bundle from its files."""
        self.reloads.append(bundle_id)
        if self.reload_error:
            raise self.reload_error
        bundle_dir = self.local_dir() / bundle_id
        if not (bundle_dir / 'bundle_metadata.json').is_file():
            raise RuntimeError('Local bundle was not found')
        prefix = f'_local/{bundle_id}/'
        loaded = {}
        for path in sorted((bundle_dir / 'filament').glob('*.json')):
            data = json.loads(path.read_text(encoding='utf-8'))
            parent = data.get('inherits', '')
            if parent not in self.profiles and parent not in self.bundle_profiles and parent not in loaded:
                raise RuntimeError('Local bundle contains an invalid preset')
            loaded[prefix + path.stem] = data
        self.bundle_profiles = {name: data for name, data in self.bundle_profiles.items()
                                if not name.startswith(prefix)}
        self.bundle_profiles.update(loaded)

    def preset_bundle(self):
        profiles = dict(self.profiles, **{name: True for name in self.bundle_profiles})
        bundle = _bundle(self.state, profiles)
        user_file = str(self.data_root / 'user' / self.folder / 'filament' / 'x.json')
        for preset in bundle.filaments.items:
            preset.config = self.profile_configs.get(preset.name, {})
            if preset.name in self.bundle_profiles:
                preset.file = str(self.local_dir() / preset.name.split('/')[1] / 'filament' / 'x.json')
            elif preset.name in self.user_profiles:
                preset.file = user_file
            else:
                preset.file = '/app/resources/profiles/x.json'
        return bundle

    def off_ui(self, call):
        if threading.current_thread() is self.ui_thread:
            self.violations.append(call)
            raise AssertionError(f'{call} called on the UI thread')

    def message(self, text, title='OrcaSlicer', buttons='ok', icon='info'):
        self.dialogs.append((text, buttons))
        return self.answer

    def begin_update(self, slot_number, preset_name=''):
        self.off_ui('begin_update')
        self.begins.append((slot_number, preset_name))
        if self.on_begin:
            self.on_begin()
        if self.begin_error:
            raise self.begin_error
        if preset_name:
            self.state['slots'][slot_number - 1] = preset_name
        target = self.target_override or self.state['slots'][slot_number - 1]
        return types.SimpleNamespace(target_name=target, commit=lambda: self.commit(slot_number))

    def commit(self, slot_number):
        self.off_ui('commit')
        if self.commit_error:
            raise self.commit_error
        self.commits.append(slot_number)
        return types.SimpleNamespace(keys=[], slot_number=slot_number)


DEFAULT_PROFILES = {'Generic PLA': True, 'Generic PETG': True, 'Other ABS': False}


def automatic_host(storage_dir, slots=('Generic PLA',), profiles=None, colour_api=False, bundles=True,
                   folder='default'):
    return UpstreamHost(storage_dir, slots, profiles or DEFAULT_PROFILES, colour_api, bundles, folder)


def legacy_host(slots=('Generic PLA',), profiles=('Generic PLA', 'Generic PETG')):
    state = {'slots': list(slots), 'colours': []}
    bundle = _bundle(state, {name: True for name in profiles})
    return types.SimpleNamespace(preset_bundle=lambda: bundle, state=state)


def settle(controller, timeout=5.0):
    """Wait for the controller's selection worker, if one is running."""
    worker = controller._selecting
    if worker is not None:
        worker.join(timeout)


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
    fake.permission_requests = []
    fake.request_permissions = lambda **kwargs: fake.permission_requests.append(kwargs)
    fake.host = host
    fake.registered = registered
    sys.modules['orca'] = fake
    return fake
