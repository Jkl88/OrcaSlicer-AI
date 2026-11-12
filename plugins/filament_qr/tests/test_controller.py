import importlib.util
import sys
import threading
import time
import types
from unittest import mock

from tests.helpers import PLUGIN_PATH, TempDirTest, automatic_host, fq, install_fake_orca, legacy_host, settle


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

        controller.handle({'type': 'use', 'code': code, 'slot': 1})
        settle(controller)
        self.assertEqual(host.violations, [])
        self.assertEqual(host.slots, ['Generic PLA', 'Generic PETG'])
        state = controller.handle({'type': 'tick'})[-1]['state']
        self.assertEqual(state['mode'], 'automatic')
        self.assertEqual(state['slots'], ['Generic PLA', 'Generic PETG'])
        self.assertEqual(state['pending'], [])
        self.assertEqual(state['recent'][0]['profile'], 'Generic PETG')
        self.assertEqual(len(state['pin']), 6)

    def test_incompatible_profile_cannot_be_bound(self):
        controller, _ = self.make()
        with self.assertRaises(ValueError):
            controller.handle({'type': 'create', 'profile': 'Other ABS', 'material': 'ABS'})

    def test_select_runs_off_ui_thread(self):
        controller, host = self.make()
        spool = controller.store.add_spool('PLA Basic', 'Printer A', 'PLA')
        host.profiles['PLA Basic'] = True
        controller.handle({'type': 'tick'})
        controller.service.scan(spool['code'], 1)
        controller.handle({'type': 'tick'})
        settle(controller)
        self.assertEqual(host.violations, [])
        self.assertEqual(host.begins, [(2, 'PLA Basic')])
        self.assertIn('Слот 2: «Generic PLA» → «PLA Basic»', host.dialogs[0][0])
        self.assertEqual(controller.store.recent_done()[0]['profile'], 'PLA Basic')

    def test_single_selection_in_flight(self):
        controller, host = self.make()
        entered, release = threading.Event(), threading.Event()
        host.on_begin = lambda: (entered.set(), release.wait(5))
        first = controller.store.add_spool('Generic PETG', 'Printer A', 'PETG')
        second = controller.store.add_spool('Generic PETG', 'Printer A', 'PETG')
        controller.service.scan(first['code'], 0)
        controller.service.scan(second['code'], 1)
        controller.handle({'type': 'tick'})
        self.assertTrue(entered.wait(5))
        controller.handle({'type': 'tick'})
        controller.handle({'type': 'use', 'code': second['code'], 'slot': 1})
        self.assertEqual(len(host.begins), 1)
        self.assertEqual([p['status'] for p in controller.store.list_pending()], ['queued', 'queued', 'queued'])
        release.set()
        settle(controller)
        host.on_begin = None
        for _ in range(3):
            controller.handle({'type': 'tick'})
            settle(controller)
        self.assertEqual(host.slots, ['Generic PETG', 'Generic PETG'])
        self.assertEqual(controller.store.list_pending(), [])

    def test_unload_during_select_drops_result(self):
        controller, host = self.make()
        entered, release = threading.Event(), threading.Event()
        host.on_begin = lambda: (entered.set(), release.wait(10))
        spool = controller.store.add_spool('Generic PETG', 'Printer A', 'PETG')
        controller.handle({'type': 'use', 'code': spool['code'], 'slot': 0})
        self.assertTrue(entered.wait(5))
        started = time.monotonic()
        controller.shutdown()
        self.assertLess(time.monotonic() - started, 4)
        release.set()
        settle(controller)
        [pending] = controller.store.list_pending()
        self.assertEqual(pending['status'], 'queued')
        self.assertEqual(controller.handle({'type': 'tick'}), [])

    def test_cancelled_scan_is_retried_on_request(self):
        controller, host = self.make()
        host.answer = 'no'
        spool = controller.store.add_spool('Generic PETG', 'Printer A', 'PETG')
        controller.handle({'type': 'use', 'code': spool['code'], 'slot': 0})
        settle(controller)
        [pending] = controller.store.list_pending()
        self.assertEqual(pending['status'], 'cancelled')

        host.answer = 'yes'
        controller.handle({'type': 'retry', 'id': pending['id']})
        settle(controller)
        self.assertEqual(controller.store.list_pending(), [])
        self.assertEqual(host.slots[0], 'Generic PETG')

    def test_server_bind_permission_error_message(self):
        controller, _ = self.make()
        with mock.patch.object(fq, '_BoundedServer', side_effect=PermissionError('not allowed')):
            controller.start_server()
        self.assertIn('«Плагины»', controller.server_error)

    def test_manual_mode_completes_when_user_selects_profile(self):
        host = legacy_host(slots=['Generic PLA'])
        host.plugin = types.SimpleNamespace(storage=lambda: str(self.tmp))
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
        self.assertEqual(fake.permission_requests,
                         [{'network_socket': ['127.0.0.1', '0.0.0.0', '192.0.2.1'], 'threading': True}])

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

    def test_storage_uses_upstream_plugin_storage(self):
        host = automatic_host(self.tmp / 'storage')
        module, _ = self.load_with_fake_orca(host)
        page = module.FilamentQRPage()
        page.on_load()
        self.addCleanup(page.on_unload)
        self.assertTrue(page.controller.available)
        self.assertTrue((self.tmp / 'storage' / 'filament_qr.sqlite3').exists())

    def test_registration_without_permission_api(self):
        module, fake = self.load_with_fake_orca(automatic_host(self.tmp))
        del fake.request_permissions
        module.FilamentQRPlugin().register_capabilities()
        self.assertEqual(fake.registered, [module.FilamentQRPage])
