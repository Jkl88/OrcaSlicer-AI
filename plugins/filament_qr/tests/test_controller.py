import importlib.util
import sys
import threading
import types

from tests.helpers import PLUGIN_PATH, TempDirTest, automatic_host, fq, install_fake_orca, legacy_host


class ControllerTest(TempDirTest):
    def make(self, host=None):
        host = host or automatic_host(self.tmp, slots=['Generic PLA', 'Generic PLA'])
        caps = fq.HostCapabilities.discover(types.SimpleNamespace(host=host))
        controller = fq.Controller(host, caps, self.tmp)
        self.addCleanup(controller.shutdown)
        return controller, host

    def test_create_use_and_state(self):
        controller, host = self.make()
        [created, label, state] = controller.handle(
            {'type': 'create', 'profile': 'Generic PETG', 'material': 'PETG', 'brand': 'eSUN'})
        self.assertEqual(created['type'], 'created')
        self.assertIn('<svg', label['svg'])
        code = created['spool']['code']

        out = controller.handle({'type': 'use', 'code': code, 'slot': 1})
        self.assertEqual(host.v1.presets.slots, ['Generic PLA', 'Generic PETG'])
        state = out[-1]['state']
        self.assertEqual(state['mode'], 'automatic')
        self.assertEqual(state['slots'], ['Generic PLA', 'Generic PETG'])
        self.assertEqual(state['pending'], [])
        self.assertEqual(state['recent'][0]['profile'], 'Generic PETG')
        self.assertEqual(len(state['pin']), 6)

    def test_incompatible_profile_cannot_be_bound(self):
        controller, _ = self.make()
        with self.assertRaises(ValueError):
            controller.handle({'type': 'create', 'profile': 'Other ABS', 'material': 'ABS'})

    def test_phone_scan_is_applied_on_next_tick(self):
        controller, host = self.make()
        spool = controller.store.add_spool('Generic PETG', 'Printer A', 'PETG')
        controller.handle({'type': 'tick'})
        controller.service.scan(spool['code'], 0)
        controller.handle({'type': 'tick'})
        self.assertEqual(host.v1.presets.slots[0], 'Generic PETG')

    def test_reentrant_tick_during_confirmation_does_not_commit_twice(self):
        controller, host = self.make()
        spool = controller.store.add_spool('Generic PETG', 'Printer A', 'PETG')
        controller.service.scan(spool['code'], 0)
        presets = host.v1.presets
        inner_commit = presets.commit

        def commit_with_nested_tick(request):
            controller.handle({'type': 'tick'})  # the page keeps ticking while the native dialog is open
            return inner_commit(request)

        presets.commit = commit_with_nested_tick
        controller.handle({'type': 'tick'})
        self.assertEqual(len(presets.commits), 1)

    def test_cancelled_scan_is_retried_on_request(self):
        controller, host = self.make()
        host.v1.presets.answer = 'cancel'
        spool = controller.store.add_spool('Generic PETG', 'Printer A', 'PETG')
        controller.handle({'type': 'use', 'code': spool['code'], 'slot': 0})
        [pending] = controller.store.list_pending()
        self.assertEqual(pending['status'], 'cancelled')

        host.v1.presets.answer = 'confirm'
        controller.handle({'type': 'retry', 'id': pending['id']})
        self.assertEqual(controller.store.list_pending(), [])
        self.assertEqual(host.v1.presets.slots[0], 'Generic PETG')

    def test_manual_mode_completes_when_user_selects_profile(self):
        host = legacy_host(slots=['Generic PLA'])
        host.v1 = types.SimpleNamespace(plugin_storage=lambda: str(self.tmp))
        host.api_info = lambda: {'features': ['plugin_storage.v1']}
        controller, _ = self.make(host)
        spool = controller.store.add_spool('Generic PETG', 'Printer A', 'PETG')
        controller.handle({'type': 'use', 'code': spool['code'], 'slot': 0})
        self.assertEqual(controller.store.list_pending()[0]['status'], 'manual')

        host.state['slots'][0] = 'Generic PETG'
        controller.handle({'type': 'tick'})
        self.assertEqual(controller.store.list_pending(), [])

    def test_lan_toggle_persists_and_restarts_server(self):
        controller, _ = self.make()
        controller.handle({'type': 'set_lan', 'enabled': False, 'port': 18765})
        self.assertEqual(controller.server.address[1], 18765)
        self.assertEqual(fq.Settings(self.tmp / 'settings.json').port, 18765)
        with self.assertRaises(ValueError):
            controller.handle({'type': 'set_lan', 'enabled': False, 'port': 80})

    def test_without_storage_the_plugin_reports_it(self):
        host = legacy_host()
        controller = fq.Controller(host, fq.HostCapabilities.discover(types.SimpleNamespace(host=host)), None)
        [state] = controller.handle({'type': 'tick'})
        self.assertFalse(state['state']['available'])
        [error] = controller.handle({'type': 'create', 'profile': 'Generic PLA'})
        self.assertEqual(error['type'], 'error')


class LifecycleTest(TempDirTest):
    def load_with_fake_orca(self, host):
        previous = sys.modules.get('orca')
        fake = install_fake_orca(host)
        self.addCleanup(lambda: sys.modules.__setitem__('orca', previous) if previous else sys.modules.pop('orca'))
        spec = importlib.util.spec_from_file_location('fq_lifecycle', PLUGIN_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, fake

    def test_load_tick_unload_twice(self):
        host = automatic_host(self.tmp)
        module, fake = self.load_with_fake_orca(host)
        module.FilamentQRPlugin().register_capabilities()
        self.assertEqual(fake.registered, [module.FilamentQRPage])

        threads_before = threading.active_count()
        for _ in range(2):
            page = module.FilamentQRPage()
            page.on_load()
            self.assertIsNotNone(page.controller.server.address)
            page.on_message({'type': 'tick'})
            self.assertEqual(page.posted[-1]['type'], 'state')
            page.on_message({'type': 'label', 'code': 'FQ-000000'})
            self.assertEqual(page.posted[-1]['type'], 'error')
            page.on_unload()
            page.on_unload()
            page.on_message({'type': 'tick'})
        self.assertLessEqual(threading.active_count(), threads_before)
        self.assertNotIn('request_permissions', PLUGIN_PATH.read_text(encoding='utf-8'))
