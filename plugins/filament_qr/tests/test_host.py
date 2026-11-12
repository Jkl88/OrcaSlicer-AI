import types

from tests.helpers import FEATURES, TempDirTest, automatic_host, fq, legacy_host


class DiscoveryTest(TempDirTest):
    def test_full_v1_host_is_automatic(self):
        caps = fq.HostCapabilities.discover(types.SimpleNamespace(host=automatic_host(self.tmp)))
        self.assertEqual(caps.mode, 'automatic')
        self.assertTrue(caps.storage)

    def test_official_host_without_api_info_is_manual(self):
        caps = fq.HostCapabilities.discover(types.SimpleNamespace(host=legacy_host()))
        self.assertEqual(caps.mode, 'manual')
        self.assertFalse(caps.storage)

    def test_raising_api_info_is_manual(self):
        host = automatic_host(self.tmp)
        host.api_info = lambda: (_ for _ in ()).throw(RuntimeError('boom'))
        self.assertEqual(fq.HostCapabilities.discover(types.SimpleNamespace(host=host)).mode, 'manual')

    def test_partial_features_are_manual(self):
        host = automatic_host(self.tmp)
        host.api_info = lambda: {'features': [f for f in FEATURES if f != 'preset_select.v1']}
        caps = fq.HostCapabilities.discover(types.SimpleNamespace(host=host))
        self.assertEqual(caps.mode, 'manual')
        self.assertTrue(caps.storage)


class AttemptTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.store = fq.Store(self.tmp / 'db.sqlite3')
        self.host = automatic_host(self.tmp, slots=['Generic PLA', 'Generic PLA'])
        self.presets = self.host.v1.presets
        self.caps = fq.HostCapabilities.discover(types.SimpleNamespace(host=self.host))

    def enqueue(self, profile, slot):
        spool = self.store.add_spool(profile, 'Printer A', 'X')
        return self.store.enqueue_scan(spool['code'], slot)

    def attempt(self, pending):
        status = fq.attempt_pending(self.host, self.store, self.store.get_pending(pending['id']), self.caps)
        return status, self.store.get_pending(pending['id'])

    def test_applied_selection_completes_the_scan(self):
        status, row = self.attempt(self.enqueue('Generic PETG', 1))
        self.assertEqual(status, 'done')
        self.assertEqual(self.presets.slots, ['Generic PLA', 'Generic PETG'])
        [request] = self.presets.commits
        self.assertEqual(request['interaction'], 'require_user_confirmation')
        self.assertEqual(request['expected_revision'], 'rev-0')
        self.assertEqual(request['operations'],
                         [{'op': 'select', 'preset_type': 'filament', 'name': 'Generic PETG', 'scope': {'slot': 1}}])
        self.assertEqual(row['host_revision'], 'rev-1')

    def test_already_active_profile_needs_no_commit(self):
        status, _ = self.attempt(self.enqueue('Generic PLA', 0))
        self.assertEqual(status, 'done')
        self.assertEqual(self.presets.commits, [])

    def test_cancel_keeps_scan_retryable(self):
        self.presets.answer = 'cancel'
        pending = self.enqueue('Generic PETG', 0)
        status, row = self.attempt(pending)
        self.assertEqual(status, 'cancelled')
        self.assertIn(row['id'], [p['id'] for p in self.store.list_pending()])

        self.presets.answer = 'confirm'
        self.store.mark(pending['id'], 'queued')
        reopened = fq.Store(self.tmp / 'db.sqlite3')
        status = fq.attempt_pending(self.host, reopened, reopened.get_pending(pending['id']), self.caps)
        self.assertEqual(status, 'done')
        self.assertEqual(self.presets.slots[0], 'Generic PETG')

    def test_conflict_requeues_with_a_fresh_revision_then_gives_up(self):
        pending = self.enqueue('Generic PETG', 0)
        self.presets.forced_result = {'status': 'conflict', 'affected': [], 'diagnostics': []}
        statuses = [self.attempt(pending)[0] for _ in range(fq.MAX_AUTO_ATTEMPTS)]
        self.assertEqual(statuses[:-1], ['queued'] * (fq.MAX_AUTO_ATTEMPTS - 1))
        self.assertEqual(statuses[-1], 'needs_attention')

    def test_mismatched_applied_result_does_not_complete(self):
        pending = self.enqueue('Generic PETG', 1)
        self.presets.forced_result = {'status': 'applied', 'revision': 'x',
                                      'affected': [{'op': 'select', 'slot': 0, 'name': 'Generic PETG'}]}
        status, row = self.attempt(pending)
        self.assertEqual(status, 'needs_attention')
        self.assertEqual(row['status'], 'needs_attention')

    def test_missing_slot_and_incompatible_profile_need_attention(self):
        self.assertEqual(self.attempt(self.enqueue('Generic PETG', 5))[0], 'needs_attention')
        self.assertEqual(self.attempt(self.enqueue('Other ABS', 0))[0], 'needs_attention')
        self.assertEqual(self.presets.commits, [])

    def test_host_failures_are_reported(self):
        for status in ('invalid', 'unsupported', 'error', 'not_found', 'incompatible', 'invalid_slot'):
            self.presets.forced_result = {'status': status, 'message': 'detail'}
            result, row = self.attempt(self.enqueue('Generic PETG', 0))
            self.assertEqual(result, 'needs_attention')
            self.assertIn('detail', row['message'])

    def test_manual_mode_never_commits(self):
        host = legacy_host(slots=['Generic PLA'])
        caps = fq.HostCapabilities.discover(types.SimpleNamespace(host=host))
        pending = self.enqueue('Generic PETG', 0)
        status = fq.attempt_pending(host, self.store, self.store.get_pending(pending['id']), caps)
        self.assertEqual(status, 'manual')
        self.assertIn('слоте 1', self.store.get_pending(pending['id'])['message'])
