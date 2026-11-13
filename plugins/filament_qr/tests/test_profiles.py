import json
import types
from pathlib import Path

from tests.helpers import TempDirTest, automatic_host, fq, settle

PREFIX = '_local/FilamentQR/'


class NameTest(TempDirTest):
    def test_bare_name_drops_bundle_folder_and_every_code_prefix(self):
        self.assertEqual(fq.bare_profile_name('Generic PETG'), 'Generic PETG')
        self.assertEqual(fq.bare_profile_name(PREFIX + 'FQ-AAAAAA Generic PETG'), 'Generic PETG')
        self.assertEqual(fq.bare_profile_name('FQ-AAAAAA FQ-BBBBBB Generic PETG'), 'Generic PETG')
        self.assertEqual(fq.bare_profile_name('My / PLA'), 'My - PLA')

    def test_spool_profile_name_has_a_single_new_prefix(self):
        self.assertEqual(fq.spool_profile_name('FQ-1A2B3C', PREFIX + 'FQ-AAAAAA Generic PETG'),
                         PREFIX + 'FQ-1A2B3C Generic PETG')


class ProfileTestCase(TempDirTest):
    def make(self, **kwargs):
        self.host = automatic_host(self.tmp, slots=['Generic PLA', 'Generic PLA'], **kwargs)
        caps = fq.HostCapabilities.discover(types.SimpleNamespace(host=self.host))
        self.controller = fq.Controller(self.host, caps, self.host.plugin.storage())
        self.addCleanup(self.controller.shutdown)
        return self.controller

    def create(self, profile='Generic PETG', **extra):
        data = dict({'type': 'create', 'profile': profile, 'material': 'PETG', 'brand': 'eSUN',
                     'color': '#112233', 'price': '1500'}, **extra)
        return self.controller.handle(data)[0]['spool']

    def bundle_dir(self):
        return self.host.local_dir() / 'FilamentQR'

    def profile_file(self, spool):
        return self.bundle_dir() / 'filament' / (spool['profile'][len(PREFIX):] + '.json')


class CreateTest(ProfileTestCase):
    def test_label_gets_its_own_profile_derived_from_the_selected_one(self):
        self.make()
        spool = self.create()
        self.assertEqual(spool['profile'], f"{PREFIX}{spool['code']} Generic PETG")
        data = json.loads(self.profile_file(spool).read_text(encoding='utf-8'))
        self.assertEqual(data['inherits'], 'Generic PETG')
        self.assertEqual(data['name'], f"{spool['code']} Generic PETG")
        self.assertEqual((data['type'], data['from'], data['instantiation']), ('filament', 'User', 'true'))
        self.assertEqual(data['filament_vendor'], ['eSUN'])
        self.assertEqual(data['default_filament_colour'], ['#112233'])
        self.assertEqual(data['filament_cost'], ['1500'])
        self.assertEqual(self.host.reloads, ['FilamentQR'])
        self.assertIn(spool['profile'], self.controller.refresh_workspace().profiles)
        metadata = json.loads((self.bundle_dir() / 'bundle_metadata.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['id'], 'FilamentQR')

    def test_label_from_a_prefixed_profile_keeps_one_new_prefix_and_the_original_parent(self):
        self.make()
        first = self.create()
        second = self.create(profile=first['profile'])
        self.assertEqual(second['profile'], f"{PREFIX}{second['code']} Generic PETG")
        data = json.loads(self.profile_file(second).read_text(encoding='utf-8'))
        self.assertEqual(data['inherits'], 'Generic PETG')

    def test_failed_reload_creates_nothing(self):
        self.make()
        self.host.reload_error = RuntimeError('A modified selected preset was removed or renamed')
        [error] = self.controller.handle({'type': 'create', 'profile': 'Generic PETG', 'material': 'PETG'})
        self.assertEqual(error['type'], 'error')
        self.assertIn('профиль', error['message'])
        self.assertEqual(self.controller.store.list_spools(), [])
        self.assertEqual(list((self.bundle_dir() / 'filament').glob('*.json')), [])

    def test_bundle_goes_to_the_folder_orca_reads(self):
        self.make(folder='4815162342')
        (self.host.data_root / 'user' / 'default').mkdir(parents=True)
        self.host.profiles['Mine'] = True
        self.host.user_profiles.add('Mine')
        spool = self.create()
        self.assertTrue(self.profile_file(spool).is_file())
        self.assertFalse((self.host.data_root / 'user' / 'default' / '_local' / 'FilamentQR').exists())

    def test_without_bundle_api_the_label_uses_the_selected_profile(self):
        self.make(bundles=False)
        spool = self.create()
        self.assertEqual(spool['profile'], 'Generic PETG')


class DeleteTest(ProfileTestCase):
    def test_deleting_the_label_deletes_its_profile(self):
        self.make()
        spool = self.create()
        self.controller.handle({'type': 'delete', 'code': spool['code']})
        self.assertIsNone(self.controller.store.get_spool(spool['code']))
        self.assertFalse(self.profile_file(spool).exists())
        self.assertNotIn(spool['profile'], self.controller.refresh_workspace().profiles)
        self.assertEqual(self.host.reloads, ['FilamentQR', 'FilamentQR'])

    def test_refused_reload_keeps_label_and_profile(self):
        self.make()
        spool = self.create()
        self.host.reload_error = RuntimeError('Preset outside this bundle depends on it: My copy')
        [error] = self.controller.handle({'type': 'delete', 'code': spool['code']})
        self.assertEqual(error['type'], 'error')
        self.assertIn('My copy', error['message'])
        self.assertIsNotNone(self.controller.store.get_spool(spool['code']))
        self.assertTrue(self.profile_file(spool).exists())

    def test_foreign_profile_is_never_deleted(self):
        self.make()
        spool = self.controller.store.add_spool('Generic PETG', 'Printer A', 'PETG')
        self.controller.handle({'type': 'delete', 'code': spool['code']})
        self.assertIsNone(self.controller.store.get_spool(spool['code']))
        self.assertEqual(self.host.reloads, [])


class MissingProfileTest(ProfileTestCase):
    def test_choosing_a_label_without_profile_offers_to_delete_it(self):
        self.make()
        spool = self.controller.store.add_spool('Vanished PLA', 'Printer A', 'PLA')
        [missing] = self.controller.handle({'type': 'use', 'code': spool['code'], 'slot': 0})
        self.assertEqual(missing, {'type': 'profile_missing', 'code': spool['code'], 'profile': 'Vanished PLA'})
        self.assertEqual(self.controller.store.list_pending(), [])
        state = self.controller.handle({'type': 'tick'})[-1]['state']
        self.assertTrue(next(s for s in state['spools'] if s['code'] == spool['code'])['missing'])

    def test_phone_scan_of_a_label_without_profile_explains_it(self):
        self.make()
        spool = self.controller.store.add_spool('Vanished PLA', 'Printer A', 'PLA')
        self.controller.service.scan(spool['code'], 0)
        self.controller.handle({'type': 'tick'})
        settle(self.controller)
        [pending] = self.controller.store.list_pending()
        self.assertEqual(pending['status'], 'needs_attention')
        self.assertIn('не найден', pending['message'])
        state = self.controller.handle({'type': 'tick'})[-1]['state']
        self.assertTrue(state['pending'][0]['missing'])
        self.assertEqual(self.host.begins, [])

    def test_existing_profile_is_not_missing(self):
        self.make()
        spool = self.create()
        state = self.controller.handle({'type': 'tick'})[-1]['state']
        self.assertFalse(state['spools'][0]['missing'])
        self.controller.handle({'type': 'use', 'code': spool['code'], 'slot': 1})
        settle(self.controller)
        self.assertEqual(self.host.slots[1], spool['profile'])
        self.assertTrue(Path(self.host.plugin.storage()).is_dir())
