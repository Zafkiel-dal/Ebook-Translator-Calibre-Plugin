import unittest

from ...lib.novel_selector import (
    ActiveEntityTracker, AliasIndex, AmbiguityResolver, ContextSelector,
    candidate_id, normalize_alias, validate_ambiguity_resolution)


class MemoryAdapter:
    def __init__(self):
        self.entities = [
            {'entity_id': 'alex', 'canonical_source': 'Alexander',
             'canonical_target': 'Aleks', 'type': 'character', 'core': True},
            {'entity_id': 'ren', 'canonical_source': 'Ren',
             'canonical_target': 'Ren', 'type': 'character'},
            {'entity_id': 'mira', 'canonical_source': 'Mira',
             'canonical_target': 'Mira', 'type': 'character'},
        ]
        self.terms = [
            {'term_id': 'mana', 'source': 'Mana Core', 'target': 'Mana'},
            {'term_id': 'order', 'source': 'Silver Order', 'target': 'Order'},
        ]

    def get_entities(self, position=None):
        return self.entities

    def get_terms(self, position=None):
        return self.terms

    def get_story_context(self, position=None):
        return [{'title': 'Chapter 1', 'summary': 'Alexander arrived.'}]

    def get_chapter_active_entity_ids(self, position=None):
        return ('mira',)

    def get_related_entity_ids(self, entity_ids, position=None):
        return ('ren',) if 'alex' in entity_ids else ()


class Resolver(AmbiguityResolver):
    def resolve(self, source_text, alias, candidate_ids, position):
        return {'entities': [{'entity_id': 'ren', 'confidence': 0.9}]}


class TestAliasIndex(unittest.TestCase):
    def test_normalization_nfkc_casefold_punctuation_and_space(self):
        self.assertEqual('lord alexander', normalize_alias('  LORD－Alexander  '))
        self.assertEqual('strasse', normalize_alias('Straße'))

    def test_multiple_aliases_point_to_one_entity(self):
        index = AliasIndex({'alex': ('Alexander', 'Lord Alexander')})
        self.assertEqual(('alex',), index.candidates('lord-alexander'))
        self.assertEqual(('alex',), index.match('LORD ALEXANDER arrived.')[0].entity_ids)

    def test_longest_boundary_aware_match(self):
        index = AliasIndex({'short': ('Alex',), 'long': ('Alexander',)})
        matches = index.match('Alexander met Alex, not Alexei.')
        self.assertEqual(['alexander', 'alex'], [match.alias for match in matches])

    def test_ambiguous_alias_is_preserved(self):
        index = AliasIndex({'alex': ('the Duke',), 'ren': ('the Duke',)})
        match = index.match('The Duke spoke.')[0]
        self.assertEqual(('alex', 'ren'), match.entity_ids)

    def test_pronouns_are_denied(self):
        index = AliasIndex({'alex': ('she', 'Alexander')})
        self.assertNotIn('she', index)
        self.assertIn('alexander', index)

    def test_unspaced_scripts_match_inside_a_sentence(self):
        index = AliasIndex({'mana': ('魔力核',), 'thai': ('มานา',)})
        self.assertEqual(('mana',), index.match('魔力核を使う。')[0].entity_ids)
        self.assertEqual(('thai',), index.match('ใช้มานาทันที')[0].entity_ids)


class TestActiveEntityTracker(unittest.TestCase):
    def test_six_chunk_decay_and_expiry(self):
        tracker = ActiveEntityTracker()
        tracker.update(('alex',))
        self.assertEqual((100, 0), tracker.scores()['alex'])
        tracker.update(())
        self.assertEqual((80, 1), tracker.scores()['alex'])
        for _unused in range(5):
            tracker.update(())
        self.assertNotIn('alex', tracker.scores())

    def test_configurable_decay_and_persistence_hooks(self):
        saved = []
        tracker = ActiveEntityTracker(window=2, decay=(9, 3),
                                      save_hook=saved.append)
        tracker.update(('alex',), {'book': 1, 'chapter': 2, 'chunk': 3})
        restored = ActiveEntityTracker(window=2, decay=(9, 3),
                                       load_hook=lambda: saved[-1])
        self.assertEqual((9, 0), restored.scores()['alex'])
        self.assertEqual((1, 2, 3), restored.snapshot()['events'][0]['position'])


class TestContextSelector(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryAdapter()
        self.index = AliasIndex({
            'alex': ('Alex', 'the Duke'), 'ren': ('the Duke',),
        })

    def test_exact_canonical_glossary_and_debug_reasons(self):
        result = ContextSelector(self.memory, self.index).select(
            'Alexander activated the Mana Core.', (1, 1, 1))
        self.assertEqual(('alex', 'mira', 'ren'), result.entity_ids)
        self.assertEqual(('mana',), result.term_ids)
        self.assertIn('exact-canonical', result.debug_reasons['entity:alex'])
        self.assertIn('exact-glossary', result.debug_reasons['term:mana'])

    def test_alias_and_ambiguous_candidates_are_both_selected_offline(self):
        result = ContextSelector(self.memory, self.index).select(
            'The Duke nodded to Alex.', None)
        self.assertEqual(('alex', 'ren', 'mira'), result.entity_ids)
        self.assertEqual(1, len(result.ambiguous_aliases))
        self.assertIn('ambiguous-alias:the duke', result.debug_reasons['entity:ren'])

    def test_optional_resolver_is_validated_and_read_only(self):
        result = ContextSelector(self.memory, self.index, ambiguity_resolver=Resolver()).select(
            'The Duke entered.', None)
        self.assertEqual(('ren', 'mira', 'alex'), result.entity_ids)
        self.assertEqual((), result.ambiguous_aliases)
        self.assertIn('ambiguous-resolved:the duke', result.debug_reasons['entity:ren'])

    def test_invalid_resolver_output_is_ignored(self):
        value = validate_ambiguity_resolution(
            {'entities': [{'entity_id': 'future', 'confidence': .9},
                          {'entity_id': 'alex', 'confidence': 2}]},
            ('alex', 'ren'))
        self.assertEqual((), value.entity_ids)

    def test_active_carryover_and_scoring_priority(self):
        tracker = ActiveEntityTracker()
        tracker.update(('mira',))
        result = ContextSelector(self.memory, self.index, tracker).select(
            'Alexander spoke.', None)
        self.assertEqual(('mira', 'alex', 'ren'), result.entity_ids)
        self.assertIn('recent-active:0', result.debug_reasons['entity:mira'])
        self.assertIn('relationship', result.debug_reasons['entity:ren'])

    def test_entity_and_term_diagnostics_have_distinct_namespaces(self):
        self.memory.entities = [{
            'entity_id': '1', 'canonical_source': 'Alice',
            'canonical_target': 'Alicia', 'type': 'character'}]
        self.memory.terms = [{
            'term_id': '1', 'source': 'Mana', 'target': 'Mana'}]
        result = ContextSelector(self.memory, AliasIndex()).select('Alice used Mana.')
        self.assertEqual(('1',), result.entity_ids)
        self.assertEqual(('1',), result.term_ids)
        self.assertIn(candidate_id('entity', '1'), result.debug_reasons)
        self.assertIn(candidate_id('term', '1'), result.debug_reasons)

    def test_chapter_core_full_and_immutable_result(self):
        selector = ContextSelector(self.memory, self.index)
        selective = selector.select('nothing named', None)
        self.assertEqual(('mira', 'alex', 'ren'), selective.entity_ids)
        full = selector.select('nothing named', None, strategy='Full')
        self.assertEqual(('mira', 'alex', 'ren'), full.entity_ids)
        with self.assertRaises(TypeError):
            full.debug_reasons['alex'] = ()
        with self.assertRaises(TypeError):
            full.entities[0]['type'] = 'changed'

    def test_invalid_strategy_rejected(self):
        with self.assertRaises(ValueError):
            ContextSelector(self.memory, self.index).select('', strategy='all')
