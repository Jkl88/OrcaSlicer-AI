import sqlite3
import threading

from tests.helpers import TempDirTest, fq


class StoreSchemaTest(TempDirTest):
    def test_new_database_gets_current_schema(self):
        store = fq.Store(self.tmp / 'db.sqlite3')
        self.assertEqual(store.schema_version(), fq.SCHEMA_VERSION)
        self.assertEqual(store.list_spools(), [])
        self.assertEqual(store.list_pending(), [])

    def test_prototype_database_keeps_spools(self):
        path = self.tmp / 'filament_qr.sqlite3'
        db = sqlite3.connect(str(path))
        db.execute('''CREATE TABLE spools (code TEXT PRIMARY KEY, profile TEXT NOT NULL, printer TEXT NOT NULL,
                      material TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                      created TEXT NOT NULL)''')
        db.execute("INSERT INTO spools VALUES ('FQ-ABCDEF','Generic PLA','Printer A','PLA','eSUN','','2026-01-01')")
        db.commit()
        db.close()

        store = fq.Store(path)
        self.assertEqual(store.schema_version(), fq.SCHEMA_VERSION)
        self.assertEqual(store.get_spool('FQ-ABCDEF')['profile'], 'Generic PLA')
        self.assertEqual(store.get_spool('FQ-ABCDEF')['color'], '')
        self.assertEqual(store.enqueue_scan('abcdef', 0)['code'], 'FQ-ABCDEF')

    def test_version_1_database_gets_colour_columns(self):
        path = self.tmp / 'filament_qr.sqlite3'
        db = sqlite3.connect(str(path))
        db.execute('''CREATE TABLE spools (code TEXT PRIMARY KEY, profile TEXT NOT NULL, printer TEXT NOT NULL,
                      material TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                      created TEXT NOT NULL)''')
        db.execute('''CREATE TABLE pending_scans (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL,
                      profile TEXT NOT NULL, slot INTEGER NOT NULL, status TEXT NOT NULL,
                      attempts INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                      host_revision TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL)''')
        db.execute("INSERT INTO spools VALUES ('FQ-ABCDEF','Generic PLA','Printer A','PLA','','','2026-01-01')")
        db.execute("INSERT INTO pending_scans (code, profile, slot, status, created, updated) "
                   "VALUES ('FQ-ABCDEF','Generic PLA',0,'queued','t','t')")
        db.execute('PRAGMA user_version = 1')
        db.commit()
        db.close()

        store = fq.Store(path)
        self.assertEqual(store.schema_version(), 2)
        self.assertEqual(store.get_spool('FQ-ABCDEF')['color'], '')
        self.assertEqual(store.list_pending()[0]['color'], '')

    def test_newer_schema_is_refused(self):
        path = self.tmp / 'db.sqlite3'
        db = sqlite3.connect(str(path))
        db.execute('PRAGMA user_version = 99')
        db.close()
        with self.assertRaises(RuntimeError):
            fq.Store(path)


class StoreSpoolTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.store = fq.Store(self.tmp / 'db.sqlite3')

    def test_code_collisions_are_retried(self):
        tokens = iter(['AAAAAA', 'AAAAAA', 'BBBBBB'])
        first = self.store.add_spool('Generic PLA', 'P', 'PLA', token=lambda: next(tokens))
        second = self.store.add_spool('Generic PLA', 'P', 'PLA', token=lambda: next(tokens))
        self.assertEqual(first['code'], 'FQ-AAAAAA')
        self.assertEqual(second['code'], 'FQ-BBBBBB')

    def test_exhausted_codes_raise(self):
        self.store.add_spool('Generic PLA', 'P', 'PLA', token=lambda: 'CCCCCC')
        with self.assertRaises(RuntimeError):
            self.store.add_spool('Generic PLA', 'P', 'PLA', token=lambda: 'CCCCCC')

    def test_colour_is_normalized_and_validated(self):
        self.assertEqual(self.store.add_spool('Generic PLA', 'P', 'PLA', color='#a1b2c3')['color'], '#A1B2C3')
        self.assertEqual(self.store.add_spool('Generic PLA', 'P', 'PLA')['color'], '')
        for bad in ('red', '#12345', '#GGGGGG', '#1234567', '"><script>'):
            with self.subTest(colour=bad), self.assertRaises(ValueError):
                self.store.add_spool('Generic PLA', 'P', 'PLA', color=bad)

    def test_scan_captures_spool_colour(self):
        spool = self.store.add_spool('Generic PLA', 'P', 'PLA', color='#FF0000')
        self.assertEqual(self.store.enqueue_scan(spool['code'], 0)['color'], '#FF0000')

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.store.add_spool('', 'P', 'PLA')
        with self.assertRaises(ValueError):
            self.store.add_spool('x' * 181, 'P', 'PLA')

    def test_concurrent_adds_produce_unique_codes(self):
        codes, errors = [], []

        def worker():
            try:
                for _ in range(10):
                    codes.append(self.store.add_spool('Generic PLA', 'P', 'PLA')['code'])
            except Exception as exc:  # collected, asserted on the main thread
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(codes), 40)
        self.assertEqual(len(set(codes)), 40)

    def test_delete(self):
        spool = self.store.add_spool('Generic PLA', 'P', 'PLA')
        self.assertTrue(self.store.delete_spool(spool['code']))
        self.assertIsNone(self.store.get_spool(spool['code']))
        self.assertFalse(self.store.delete_spool(spool['code']))


class PendingQueueTest(TempDirTest):
    def test_pending_scans_survive_reopen(self):
        path = self.tmp / 'db.sqlite3'
        store = fq.Store(path)
        spool = store.add_spool('Generic PETG', 'P', 'PETG')
        pending = store.enqueue_scan(spool['code'], 2)
        store.mark(pending['id'], 'cancelled', 'user said no', attempted=True)

        reopened = fq.Store(path)
        [row] = reopened.list_pending()
        self.assertEqual(row['id'], pending['id'])
        self.assertEqual(row['slot'], 2)
        self.assertEqual(row['status'], 'cancelled')
        self.assertEqual(row['attempts'], 1)
        self.assertEqual(row['message'], 'user said no')

    def test_scans_are_independent_and_ordered(self):
        store = fq.Store(self.tmp / 'db.sqlite3')
        a = store.add_spool('Generic PLA', 'P', 'PLA')
        b = store.add_spool('Generic PETG', 'P', 'PETG')
        first = store.enqueue_scan(a['code'], 0)
        second = store.enqueue_scan(b['code'], 1)
        self.assertEqual(store.next_queued()['id'], first['id'])
        store.mark(first['id'], 'done')
        self.assertEqual(store.next_queued()['id'], second['id'])
        self.assertEqual(store.get_pending(second['id'])['profile'], 'Generic PETG')
        self.assertEqual([p['id'] for p in store.recent_done()], [first['id']])

    def test_unknown_spool_is_not_queued(self):
        store = fq.Store(self.tmp / 'db.sqlite3')
        with self.assertRaises(LookupError):
            store.enqueue_scan('FQ-000000', 0)
        self.assertEqual(store.list_pending(), [])
