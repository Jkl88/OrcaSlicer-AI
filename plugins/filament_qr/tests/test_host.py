import threading
import types

from tests.helpers import TempDirTest, automatic_host, fq, legacy_host


def in_worker(fn, *args):
    """Run fn on a non-UI thread, as the controller does, and return its result."""
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault('value', fn(*args)))
    worker.start()
    worker.join(5)
    return result['value']


class DiscoveryTest(TempDirTest):
    def discover(self, host):
        return fq.HostCapabilities.discover(types.SimpleNamespace(host=host))

    def test_caps_upstream_automatic(self):
        caps = self.discover(automatic_host(self.tmp))
        self.assertEqual(caps.mode, 'automatic')
        self.assertTrue(caps.storage)

    def test_caps_bundle_only_manual(self):
        caps = self.discover(legacy_host())
        self.assertEqual(caps.mode, 'manual')
        self.assertFalse(caps.storage)

    def test_caps_v1_only_is_manual_without_storage(self):
        host = legacy_host()
        host.api_info = lambda: {'features': ['plugin_storage.v1', 'preset_select.v1']}
        host.v1 = types.SimpleNamespace(plugin_storage=lambda: str(self.tmp),
                                        presets=types.SimpleNamespace(snapshot=dict, commit=dict))
        caps = self.discover(host)
        self.assertEqual(caps.mode, 'manual')
        self.assertFalse(caps.storage)

    def test_colour_api_is_detected(self):
        self.assertFalse(self.discover(automatic_host(self.tmp)).colour)
        self.assertTrue(self.discover(automatic_host(self.tmp, colour_api=True)).colour)
        self.assertFalse(self.discover(legacy_host()).colour)

    def test_no_host(self):
        caps = fq.HostCapabilities.discover(types.SimpleNamespace())
        self.assertEqual((caps.mode, caps.storage), ('manual', False))


class WorkspaceTest(TempDirTest):
    def test_reads_slots_and_compatible_profiles(self):
        ws = fq.read_workspace(automatic_host(self.tmp, slots=['Generic PLA', 'Generic PETG']), None)
        self.assertEqual(ws.printer, 'Printer A')
        self.assertEqual(ws.slots, ['Generic PLA', 'Generic PETG'])
        self.assertEqual(ws.profiles, ['Generic PLA', 'Generic PETG'])
        self.assertEqual(ws.colours, ['#FFFFFF', '#FFFFFF'])

    def test_colours_parse_quoted_values_and_tolerate_missing_api(self):
        host = automatic_host(self.tmp, slots=['A', 'B'])
        host.state['colours'] = ['"#ff0000"', '#00FF00']
        self.assertEqual(fq.read_workspace(host, None).colours, ['#FF0000', '#00FF00'])
        self.assertEqual(fq.read_workspace(legacy_host(), None).colours, [])


class PrepareTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.store = fq.Store(self.tmp / 'db.sqlite3')
        self.host = automatic_host(self.tmp, slots=['Generic PLA', 'Generic PLA'])
        self.caps = fq.HostCapabilities.discover(types.SimpleNamespace(host=self.host))

    def prepare(self, profile, slot, host=None, caps=None, color=''):
        spool = self.store.add_spool(profile, 'Printer A', 'X', color=color)
        pending = self.store.enqueue_scan(spool['code'], slot)
        status = fq.prepare_pending(host or self.host, self.store, self.store.get_pending(pending['id']),
                                    caps or self.caps)
        return status, self.store.get_pending(pending['id'])

    def test_needs_selection_stays_queued(self):
        status, row = self.prepare('Generic PETG', 1)
        self.assertEqual(status, 'select')
        self.assertEqual(row['status'], 'queued')
        self.assertEqual(self.host.begins, [])

    def test_already_active_profile_is_done(self):
        status, row = self.prepare('Generic PLA', 0)
        self.assertEqual((status, row['status']), ('done', 'done'))

    def test_same_profile_with_other_colour_needs_selection_when_host_sets_colours(self):
        host = automatic_host(self.tmp, slots=['Generic PLA'], colour_api=True)
        caps = fq.HostCapabilities.discover(types.SimpleNamespace(host=host))
        self.assertEqual(self.prepare('Generic PLA', 0, host, caps, '#FF0000')[0], 'select')
        self.assertEqual(self.prepare('Generic PLA', 0, host, caps, '#ffffff')[0], 'done')
        self.assertEqual(self.prepare('Generic PLA', 0, color='#FF0000')[0], 'done')

    def test_missing_slot_and_incompatible_profile_need_attention(self):
        self.assertEqual(self.prepare('Generic PETG', 5)[0], 'needs_attention')
        self.assertEqual(self.prepare('Other ABS', 0)[0], 'needs_attention')
        self.assertEqual(self.host.begins, [])

    def test_manual_mode_asks_user(self):
        host = legacy_host(slots=['Generic PLA'])
        caps = fq.HostCapabilities.discover(types.SimpleNamespace(host=host))
        status, row = self.prepare('Generic PETG', 0, host, caps)
        self.assertEqual(status, 'manual')
        self.assertIn('слоте 1', row['message'])


class SelectTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.host = automatic_host(self.tmp, slots=['Generic PLA', 'Generic PLA'])

    def select(self, slot=1, profile='Generic PETG', current='Generic PLA'):
        return in_worker(fq.select_in_slot, self.host, slot, profile, current)

    def test_confirmed_selection_uses_one_based_slot(self):
        status, _ = self.select()
        self.assertEqual(status, 'done')
        self.assertEqual(self.host.begins, [(2, 'Generic PETG')])
        self.assertEqual(self.host.commits, [2])
        self.assertEqual(self.host.slots, ['Generic PLA', 'Generic PETG'])
        text, buttons = self.host.dialogs[0]
        self.assertEqual(buttons, 'yes_no')
        self.assertIn('Слот 2: «Generic PLA» → «Generic PETG»', text)

    def test_select_declined_marks_cancelled(self):
        self.host.answer = 'no'
        self.assertEqual(self.select()[0], 'cancelled')
        self.assertEqual(self.host.begins, [])

    def test_select_cancelled_prompt_marks_cancelled(self):
        self.host.begin_error = RuntimeError('cancelled')
        self.assertEqual(self.select()[0], 'cancelled')
        self.assertEqual(self.host.commits, [])

    def test_select_value_error_marks_needs_attention(self):
        self.host.begin_error = ValueError('Filament preset "Generic PETG" is not offered by slot 2')
        status, message = self.select()
        self.assertEqual(status, 'needs_attention')
        self.assertIn('not offered', message)

    def test_changed_slot_mapping_needs_attention(self):
        self.host.commit_error = RuntimeError('Filament slot 2 now holds "X"')
        status, message = self.select()
        self.assertEqual(status, 'needs_attention')
        self.assertIn('изменилось', message)

    def test_other_target_needs_attention(self):
        self.host.target_override = 'Something else'
        self.assertEqual(self.select()[0], 'needs_attention')

    def test_colour_is_set_after_selection_when_supported(self):
        host = automatic_host(self.tmp, slots=['Generic PLA', 'Generic PLA'], colour_api=True)
        status, message = in_worker(fq.select_in_slot, host, 1, 'Generic PETG', 'Generic PLA', '#FF0000')
        self.assertEqual(status, 'done')
        self.assertEqual(host.violations, [])
        self.assertEqual(host.state['colours'], ['#FFFFFF', '#FF0000'])
        self.assertIn('цвет', message)
        self.assertIn('#FF0000', host.dialogs[0][0])

    def test_colour_without_host_support_is_left_to_the_user(self):
        status, message = in_worker(fq.select_in_slot, self.host, 1, 'Generic PETG', 'Generic PLA', '#FF0000')
        self.assertEqual(status, 'done')
        self.assertIn('#FF0000', message)
        self.assertIn('вручную', message)

    def test_colour_failure_needs_attention(self):
        host = automatic_host(self.tmp, slots=['Generic PLA', 'Generic PLA'], colour_api=True)
        host.colour_error = RuntimeError('no slot')
        status, message = in_worker(fq.select_in_slot, host, 1, 'Generic PETG', 'Generic PLA', '#FF0000')
        self.assertEqual(status, 'needs_attention')
        self.assertIn('цвет', message)

    def test_permission_error_is_explained(self):
        self.host.begin_error = PermissionError('denied')
        status, message = self.select()
        self.assertEqual(status, 'needs_attention')
        self.assertIn('«Плагины»', message)
