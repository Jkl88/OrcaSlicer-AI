import importlib.util
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
    def __init__(self, name, compatible=True):
        self.name, self.is_visible, self.is_compatible, self.is_dirty = name, True, compatible, False


class _Filaments:
    def __init__(self, profiles):
        self.items = [_Preset(n, c) for n, c in profiles.items()]

    def size(self):
        return len(self.items)

    def preset(self, i):
        return self.items[i]

    def edited_preset(self):
        return self.items[0]


def _bundle(state, profiles):
    return types.SimpleNamespace(
        filaments=_Filaments(profiles),
        printers=types.SimpleNamespace(selected_preset_name=lambda: 'Printer A'),
        current_filament_preset_names=lambda: list(state['slots']))


class UpstreamHost:
    """OrcaSlicer 2.5.0 surface. begin_update/commit raise when called on the thread that built the host."""

    def __init__(self, storage_dir, slots, profiles):
        self.ui_thread = threading.current_thread()
        self.state = {'slots': list(slots)}
        self.profiles = dict(profiles)
        self.answer = 'yes'
        self.dialogs, self.begins, self.commits, self.violations = [], [], [], []
        self.on_begin = self.begin_error = self.commit_error = self.target_override = None
        self.plugin = types.SimpleNamespace(storage=lambda: str(storage_dir))
        self.ui = types.SimpleNamespace(message=self.message)
        self.preset = types.SimpleNamespace(filament=types.SimpleNamespace(begin_update=self.begin_update))

    @property
    def slots(self):
        return self.state['slots']

    def preset_bundle(self):
        return _bundle(self.state, self.profiles)

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


def automatic_host(storage_dir, slots=('Generic PLA',), profiles=None):
    return UpstreamHost(storage_dir, slots, profiles or DEFAULT_PROFILES)


def legacy_host(slots=('Generic PLA',), profiles=('Generic PLA', 'Generic PETG')):
    state = {'slots': list(slots)}
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
