import json
import os
import tempfile
import unittest
from decimal import Decimal

from lib.novel_memory import (
    MemoryDescriptor, MemoryStore, SCHEMA_VERSION, StoryPosition,
    scope_database_path,
)


class NovelMemoryStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.descriptor = MemoryDescriptor.book(
            'book-1', 'English', 'Italian', series_id='series-1')
        self.path = scope_database_path(self.temp.name, self.descriptor)
        self.store = MemoryStore(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_position_orders_after_chapter_inclusively(self):
        before = StoryPosition(2, 'in_chapter', 4)
        after = StoryPosition.after_chapter(2)
        self.assertLess(before, after)
        self.assertLess(after, StoryPosition(3, 'before_chapter'))
        self.assertEqual(after, StoryPosition.from_json(after.to_json()))
        with self.assertRaises(ValueError):
            StoryPosition(1, 'later')

    def test_descriptor_and_path_are_json_safe_and_stay_under_root(self):
        descriptor = MemoryDescriptor.book('../a/b', 'EN/US', '../../Italian')
        restored = MemoryDescriptor.from_json(json.dumps(descriptor.to_json()))
        path = scope_database_path(self.temp.name, restored)
        self.assertEqual(descriptor, restored)
        self.assertTrue(os.path.commonpath([self.temp.name, path]) == self.temp.name)
        self.assertNotIn('..', os.path.basename(path))
        self.assertEqual(path, scope_database_path(self.temp.name, restored))

    def test_schema_metadata_and_entities_aliases_and_terms(self):
        self.assertEqual(SCHEMA_VERSION, self.store.schema_version)
        self.assertEqual(str(SCHEMA_VERSION), self.store.get_metadata('schema_version'))
        self.store.set_metadata('writer', 'test')
        self.assertEqual('test', self.store.get_metadata('writer'))
        entity = self.store.create_entity(self.descriptor, 'Elizabeth', 'character')
        self.store.add_alias(self.descriptor, entity, 'Liz')
        self.assertEqual(entity, self.store.find_entity(self.descriptor, 'Liz'))
        term = self.store.create_term(self.descriptor, 'tea', 'food')
        self.store.write_term_name(self.descriptor, term, 'tea', 'te',
                                   StoryPosition.after_chapter(1), 'confirmed')
        self.assertEqual('te', self.store.resolve_term_name(
            self.descriptor, term, StoryPosition(1, 'after'))['target_name'])

    def test_effective_name_has_no_future_leakage_and_deterministic_priority(self):
        entity = self.store.create_entity(self.descriptor, 'Alice')
        self.store.write_entity_name(self.descriptor, entity, 'Alice', 'Alicia',
                                     StoryPosition.after_chapter(1), 'inferred')
        self.store.write_entity_name(self.descriptor, entity, 'Alice', 'Alice Confirmed',
                                     StoryPosition.after_chapter(2), 'confirmed')
        self.store.write_entity_name(self.descriptor, entity, 'Alice', 'Future',
                                     StoryPosition.after_chapter(3), 'confirmed')
        chapter_one = self.store.resolve_entity_name(
            self.descriptor, entity, StoryPosition.after_chapter(1))
        self.assertEqual('Alicia', chapter_one['target_name'])
        chapter_two = self.store.resolve_entity_name(
            self.descriptor, entity, StoryPosition.after_chapter(2))
        self.assertEqual('Alice Confirmed', chapter_two['target_name'])
        self.store.lock_entity_name(self.descriptor, entity, 'Locked Alice')
        self.assertEqual('Locked Alice', self.store.resolve_entity_name(
            self.descriptor, entity, StoryPosition(0))['target_name'])

    def test_facts_and_state_are_append_only_and_position_filtered(self):
        entity = self.store.create_entity(self.descriptor, 'Nora')
        self.store.append_entity_fact(self.descriptor, entity, 'role', 'doctor',
                                      StoryPosition.after_chapter(1), 'confirmed')
        self.store.append_entity_fact(self.descriptor, entity, 'role', 'captain',
                                      StoryPosition.after_chapter(2), 'inferred')
        self.store.append_state_event(self.descriptor, entity, {'place': 'Rome'},
                                      StoryPosition.after_chapter(1), 'confirmed')
        self.store.append_state_event(self.descriptor, entity, {'place': 'Milan'},
                                      StoryPosition.after_chapter(2), 'confirmed')
        at_one = self.store.effective_entity(self.descriptor, entity,
                                             StoryPosition.after_chapter(1))
        self.assertEqual(1, len(at_one['facts']))
        self.assertEqual({'place': 'Rome'}, at_one['state']['value'])

    def test_user_locked_fact_wins_without_deleting_model_history(self):
        entity = self.store.create_entity(self.descriptor, 'Tara')
        self.store.append_entity_fact(
            self.descriptor, entity, 'title', 'Captain',
            StoryPosition.after_chapter(1), 'confirmed')
        self.store.append_entity_fact(
            self.descriptor, entity, 'title', 'Commander',
            StoryPosition.after_chapter(2), 'inferred')
        self.store.lock_fact(self.descriptor, entity, 'title', 'Admiral')
        record = self.store.effective_entity(
            self.descriptor, entity, StoryPosition(1))
        self.assertEqual('Admiral', record['facts'][0]['value'])
        count = self.store.connection.execute(
            'SELECT COUNT(*) FROM entity_facts WHERE entity_id=?', (entity,)
        ).fetchone()[0]
        self.assertEqual(2, count)

    def test_merge_is_atomic_and_idempotent_by_chapter_fingerprint(self):
        extraction = {
            'summary': 'The ship leaves port.',
            'entities': [{'name': 'Mara', 'translation': 'Mara',
                          'aliases': ['Captain Mara'], 'facts': [
                              {'predicate': 'rank', 'value': 'captain'}]}],
            'terms': [{'source': 'ship', 'translation': 'nave'}],
            'mentions': [{'text': 'Mara boards the ship.'}],
        }
        first = self.store.merge_chapter(self.descriptor, 1, extraction, 'hash-1')
        second = self.store.merge_chapter(self.descriptor, 1, extraction, 'hash-1')
        self.assertTrue(first['merged'])
        self.assertFalse(second['merged'])
        self.assertEqual(first['run_id'], second['run_id'])
        self.assertEqual(1, len(self.store.get_summaries(self.descriptor)))
        self.assertEqual(1, len(self.store.read_activity_mentions(self.descriptor)))
        entity = self.store.find_entity(self.descriptor, 'Captain Mara')
        self.assertEqual('Mara', self.store.resolve_entity_name(
            self.descriptor, entity, StoryPosition.after_chapter(1))['target_name'])

    def test_activity_mentions_round_trip_without_future_leakage(self):
        self.store.write_activity_mention(self.descriptor, 'first', StoryPosition(1, offset=2))
        self.store.write_activity_mention(self.descriptor, 'later', StoryPosition(2, offset=1))
        mentions = self.store.get_activity_mentions(
            self.descriptor, StoryPosition.after_chapter(1))
        self.assertEqual(['first'], [item['mention_text'] for item in mentions])

    def test_end_snapshot_and_prior_book_snapshot_lookup(self):
        prior_book = MemoryDescriptor.book(
            'book-1', 'English', 'Italian', 'series-1', book_order=1)
        self.store.write_end_of_book_snapshot(prior_book, 'Book one ending.')
        current = MemoryDescriptor.book(
            'book-2', 'English', 'Italian', 'series-1', book_order=2)
        self.store.write_end_of_book_snapshot(current, 'Book two ending.')
        prior = self.store.get_prior_book_snapshots(current)
        self.assertEqual(['book-1'], [item['book_id'] for item in prior])
        self.assertEqual('Book two ending.', self.store.get_end_of_book_snapshot(
            current)['content'])

    def test_pr590_migration_accepts_cache_adapter_and_is_idempotent(self):
        class LegacyCache(object):
            def __init__(self):
                self.values = {
                    'novel_summaries': json.dumps([
                        {'chapter': 1, 'title': 'One', 'summary': 'Old context'}]),
                    'novel_glossary': json.dumps({
                        'Frodo': {'translation': 'Frodo', 'type': 'character'}}),
                }

            def get_info(self, key):
                return self.values.get(key)

        first = self.store.migrate_pr590(LegacyCache(), self.descriptor)
        second = self.store.migrate_pr590(LegacyCache(), self.descriptor)
        self.assertEqual({'summaries': 1, 'terms': 1, 'imported': True}, first)
        self.assertEqual({'summaries': 0, 'terms': 0, 'imported': False}, second)
        term_id = self.store.connection.execute(
            'SELECT id FROM terms WHERE source_name=?', ('Frodo',)).fetchone()['id']
        self.assertEqual('Frodo', self.store.resolve_term_name(
            self.descriptor, term_id, StoryPosition(1))['target_name'])

    def test_discovery_position_hides_future_entities_aliases_and_terms(self):
        future = StoryPosition.after_chapter(10, book_order='1.5')
        entity = self.store.create_entity(self.descriptor, 'Lord Alexander',
                                          position=future)
        self.store.add_alias(self.descriptor, entity, 'Alex', future)
        term = self.store.create_term(self.descriptor, 'Moon Blade',
                                      position=future)
        self.store.write_term_name(self.descriptor, term, 'Moon Blade', 'Lama lunare',
                                   future, 'confirmed')
        earlier = StoryPosition(1, book_order='1.5')
        self.assertIsNone(self.store.find_entity(self.descriptor, 'Alex', earlier))
        self.assertFalse(self.store.resolve_entity_candidates(
            self.descriptor, 'Lord Alexander', earlier))
        self.assertIsNone(self.store.resolve_term_name(self.descriptor, term, earlier))
        self.assertEqual(entity, self.store.find_entity(
            self.descriptor, 'alex', StoryPosition.after_chapter(10, '1.5')))
        self.assertEqual('Lama lunare', self.store.resolve_term_name(
            self.descriptor, term, StoryPosition.after_chapter(11, '1.5'))['target_name'])

    def test_decimal_book_order_is_not_float_chronology(self):
        self.assertEqual('1.0000000000000000001',
                         StoryPosition(1, book_order='1.0000000000000000001').book_order)
        self.assertLess(StoryPosition(1, book_order='1.0000000000000000001'),
                        StoryPosition(1, book_order='1.0000000000000000002'))
        self.assertEqual(Decimal('2.5'), Decimal(MemoryDescriptor.book(
            'book', book_order='2.500').book_order))

    def test_keyed_state_events_preserve_unrelated_keys_and_hide_future_values(self):
        entity = self.store.create_entity(self.descriptor, 'Nora')
        self.store.append_state_event(self.descriptor, entity,
                                      {'location': 'Rome'},
                                      StoryPosition.after_chapter(1), 'confirmed')
        self.store.append_state_event(self.descriptor, entity,
                                      {'health': 'injured'},
                                      StoryPosition.after_chapter(2), 'confirmed')
        self.store.append_state_event(self.descriptor, entity,
                                      {'health': 'recovered'},
                                      StoryPosition.after_chapter(20), 'confirmed')
        at_two = self.store.effective_entity(
            self.descriptor, entity, StoryPosition.after_chapter(2))
        self.assertEqual({'location': 'Rome', 'health': 'injured'},
                         at_two['state']['value'])
        at_nine = self.store.effective_entity(
            self.descriptor, entity, StoryPosition.after_chapter(9))
        self.assertEqual('injured', at_nine['state']['value']['health'])

    def test_user_locks_are_absolute_and_scope_ownership_is_checked(self):
        entity = self.store.create_entity(self.descriptor, 'Mira')
        self.store.lock_entity_name(self.descriptor, entity, 'Locked Mira')
        self.store.write_entity_name(self.descriptor, entity, 'Mira', 'Future Mira',
                                     StoryPosition.after_chapter(99), 'confirmed')
        self.assertEqual('Locked Mira', self.store.resolve_entity_name(
            self.descriptor, entity, StoryPosition(0))['target_name'])
        other = MemoryDescriptor.book('other', 'English', 'Italian')
        with self.assertRaises(ValueError):
            self.store.append_entity_fact(other, entity, 'role', 'spy')
        with self.assertRaises(ValueError):
            self.store.add_alias(other, entity, 'Wrong scope')

    def test_model_validation_skips_invalid_siblings_and_rejects_user_fields(self):
        result = self.store.merge_chapter(self.descriptor, 1, {
            'entities': [
                {'source': 'Alice', 'translation': 'Alicia',
                 'confidence': 'confirmed'},
                {'source': 'Bob', 'translation': 'Roberto',
                 'timeless': True},
                {'source': 'Cara', 'translation': 'Cara',
                 'data': {'core': True}},
                {'source': 'Dana', 'translation': 'Dana',
                 'confidence': 'user_locked'},
            ],
            'terms': [{'source': 'sword', 'translation': 'spada',
                       'confidence': 'confirmed'}],
        }, 'model-validation')
        self.assertTrue(result['merged'])
        self.assertGreaterEqual(result['rejected'], 3)
        self.assertIsNotNone(self.store.find_entity(
            self.descriptor, 'Alice', StoryPosition.after_chapter(1)))
        self.assertIsNone(self.store.find_entity(
            self.descriptor, 'Bob', StoryPosition.after_chapter(1)))
        self.assertEqual(3, len(self.store.list_unresolved_proposals(self.descriptor)))

    def test_ambiguous_alias_is_unresolved_and_model_order_is_deterministic(self):
        first = self.store.create_entity(self.descriptor, 'Alexander One')
        second = self.store.create_entity(self.descriptor, 'Alexander Two')
        self.store.add_alias(self.descriptor, first, 'the Duke')
        self.store.add_alias(self.descriptor, second, 'the Duke')
        result = self.store.merge_chapter(self.descriptor, 1, {
            'facts': [{'entity': 'the Duke', 'key': 'rank', 'value': 'duke'}],
            'entities': [
                {'source': 'Zed', 'translation': 'Zeta'},
                {'source': 'Amy', 'translation': 'Amelia'},
            ],
        }, 'ambiguous')
        self.assertEqual(1, result['unresolved'])
        self.assertEqual('ambiguous_identity', self.store.list_unresolved_proposals(
            self.descriptor)[0]['reason'])
        names = [row['canonical_name'] for row in self.store.connection.execute(
            'SELECT canonical_name FROM entities WHERE scope_id=? ORDER BY id',
            (self.store._scope_id(self.descriptor),))]
        self.assertLess(names.index('Amy'), names.index('Zed'))

    def test_legacy_migration_identity_is_immutable_when_payload_evolves(self):
        class LegacyCache(object):
            cache_identity = 'stable-cache-1'

            def __init__(self):
                self.info = {'novel_summaries': [{'chapter': 1, 'summary': 'One'}],
                             'novel_glossary': {'blade': 'lama'},
                             'novel_progress': 1}

        cache = LegacyCache()
        self.assertTrue(self.store.migrate_pr590(cache, self.descriptor)['imported'])
        cache.info['novel_summaries'].append({'chapter': 2, 'summary': 'Two'})
        cache.info['novel_glossary']['shield'] = 'scudo'
        self.assertFalse(self.store.migrate_pr590(cache, self.descriptor)['imported'])
        self.assertEqual(1, len(self.store.get_summaries(self.descriptor)))
        self.assertEqual(1, self.store.connection.execute('SELECT COUNT(*) FROM terms').fetchone()[0])

    def test_checkpoint_reconciliation_repairs_ahead_and_behind_cache_progress(self):
        self.store.record_chapter_checkpoint(self.descriptor, 1, 'one')
        self.store.record_chapter_checkpoint(self.descriptor, 2, 'two')
        cache = {'novel_progress': 5}
        ahead = self.store.reconcile_cache_progress(cache, self.descriptor)
        self.assertEqual(2, ahead['effective_progress'])
        self.assertEqual(2, cache['novel_progress'])
        cache['novel_progress'] = 1
        behind = self.store.reconcile_cache_progress(cache, self.descriptor)
        self.assertEqual(2, behind['effective_progress'])
        self.assertEqual(2, cache['novel_progress'])

    def test_activity_is_idempotent_per_chunk_and_later_rows_do_not_leak(self):
        entities = [self.store.create_entity(self.descriptor, 'Entity %d' % index)
                    for index in range(6)]
        at_one = StoryPosition(1, offset=4)
        self.store.record_chunk_activity(self.descriptor, at_one, entities, 'chunk-1')
        self.store.record_chunk_activity(self.descriptor, at_one, entities, 'chunk-1')
        self.store.record_chunk_activity(self.descriptor, StoryPosition(5, offset=1),
                                         [entities[0]], 'chunk-5')
        rows = self.store.read_activity_mentions(self.descriptor,
                                                 StoryPosition.after_chapter(1),
                                                 'entity')
        self.assertEqual(6, len(rows))
        self.assertEqual({4}, {row['chunk'] for row in rows})
        self.assertEqual({1}, {row['chapter'] for row in rows})

    def test_v3_file_is_archived_and_future_schema_is_rejected_without_mutation(self):
        self.store.close()
        legacy = self.path.replace('.v4.sqlite3', '.sqlite3')
        connection = __import__('sqlite3').connect(legacy)
        connection.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        connection.execute('INSERT INTO metadata VALUES (?,?)', ('schema_version', '3'))
        connection.commit()
        connection.close()
        self.store = MemoryStore(self.path)
        archives = [name for name in os.listdir(os.path.dirname(legacy))
                    if '.v3.' in name and name.endswith('.archive.sqlite3')]
        self.assertTrue(archives)
        self.assertEqual(SCHEMA_VERSION, self.store.schema_version)
        self.store.close()
        future = os.path.join(self.temp.name, 'future.v4.sqlite3')
        connection = __import__('sqlite3').connect(future)
        connection.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        connection.execute('INSERT INTO metadata VALUES (?,?)', ('schema_version', '999'))
        connection.commit()
        connection.close()
        with self.assertRaises(RuntimeError):
            MemoryStore(future)
        self.assertTrue(os.path.exists(future))
        self.store = MemoryStore(self.path)

    def test_archived_explicit_user_lock_is_reimported_with_auditable_origin(self):
        self.store.close()
        legacy = self.path.replace('.v4.sqlite3', '.sqlite3')
        connection = __import__('sqlite3').connect(legacy)
        connection.executescript('''
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE scopes (id INTEGER PRIMARY KEY, series_id TEXT, book_id TEXT, scope TEXT);
            CREATE TABLE entities (id INTEGER PRIMARY KEY, scope_id INTEGER, canonical_name TEXT);
            CREATE TABLE terms (id INTEGER PRIMARY KEY, scope_id INTEGER, source_name TEXT);
            CREATE TABLE user_locks (id INTEGER PRIMARY KEY, scope_id INTEGER,
                subject_kind TEXT, subject_id INTEGER, field_name TEXT, value_json TEXT);
        ''')
        connection.execute('INSERT INTO metadata VALUES (?,?)', ('schema_version', '3'))
        connection.execute('INSERT INTO scopes VALUES (1,?,?,?)',
                           (self.descriptor.series_id, self.descriptor.book_id,
                            self.descriptor.scope))
        connection.execute('INSERT INTO entities VALUES (1,1,?)', ('Mira',))
        connection.execute('INSERT INTO user_locks VALUES (1,1,?,?,?,?)',
                           ('entity', 1, 'name',
                            json.dumps({'target_name': 'Mira Locked'})))
        connection.commit()
        connection.close()
        self.store = MemoryStore(self.path)
        imported = self.store.import_archived_user_locks(self.descriptor)
        self.assertEqual({'imported': 1, 'unresolved': 0}, imported)
        entity = self.store.find_entity(self.descriptor, 'Mira', StoryPosition(0))
        self.assertEqual('Mira Locked', self.store.resolve_entity_name(
            self.descriptor, entity, StoryPosition(0))['target_name'])
        origin = self.store.connection.execute(
            'SELECT archived_origin FROM user_locks').fetchone()['archived_origin']
        self.assertIn('.archive.sqlite3', origin)


if __name__ == '__main__':
    unittest.main()
