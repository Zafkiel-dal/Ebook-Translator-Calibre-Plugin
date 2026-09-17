import unittest

from ...lib.novel_context import ContextRenderer
from ...lib.novel_selector import AliasIndex, ContextSelector
from .test_novel_selector import MemoryAdapter


def words(text):
    return len(text.split())


class TestContextRenderer(unittest.TestCase):
    def setUp(self):
        memory = MemoryAdapter()
        index = AliasIndex({'alex': ('Alex',)})
        self.selection = ContextSelector(memory, index).select(
            'Alexander uses the Mana Core.', None, strategy='Hybrid')

    def test_requires_supplied_estimator(self):
        with self.assertRaises(TypeError):
            ContextRenderer(None)

    def test_renders_compact_sections(self):
        text = ContextRenderer(words).render(self.selection, 100)
        self.assertIn('[RELEVANT CHARACTERS]', text)
        self.assertIn('Alexander | Aleks | character', text)
        self.assertIn('[RELEVANT TERMS]', text)
        self.assertIn('Mana Core -> Mana', text)
        self.assertIn('[STORY CONTEXT]', text)

    def test_selective_omits_story_hybrid_includes_it(self):
        renderer = ContextRenderer(words)
        selective = renderer.render(self.selection, 100, strategy='Selective')
        hybrid = renderer.render(self.selection, 100, strategy='Hybrid')
        self.assertNotIn('[STORY CONTEXT]', selective)
        self.assertIn('[STORY CONTEXT]', hybrid)

    def test_full_uses_selector_complete_set(self):
        memory = MemoryAdapter()
        result = ContextSelector(memory, AliasIndex()).select('', strategy='Full')
        text = ContextRenderer(words).render(result, 100, strategy='Full')
        self.assertIn('Ren | Ren | character', text)
        self.assertIn('Silver Order -> Order', text)

    def test_budget_is_strict_and_items_are_atomic(self):
        renderer = ContextRenderer(words)
        result = renderer.render_result(self.selection, 4)
        self.assertLessEqual(words(result.text), 4)
        self.assertTrue(result.skipped)
        self.assertNotIn('Mana Core ->', result.text)

    def test_priority_prefers_exact_term_before_story_and_active(self):
        renderer = ContextRenderer(words)
        result = renderer.render_result(self.selection, 9)
        self.assertIn('term:mana', result.included)
        self.assertNotIn('story:0', result.included)
        self.assertLessEqual(result.tokens, 9)

    def test_entity_identity_is_atomic_before_optional_details(self):
        memory = MemoryAdapter()
        memory.entities = [{
            'entity_id': 'alex', 'canonical_source': 'Alexander',
            'canonical_target': 'Aleks', 'type': 'character',
            'facts': {'title': 'Duke', 'status': 'injured'},
        }]
        memory.terms = []
        selection = ContextSelector(memory, AliasIndex()).select('Alexander', strategy='Selective')
        renderer = ContextRenderer(words)
        compact = renderer.render_result(selection, 7)
        self.assertIn('Alexander | Aleks | character', compact.text)
        self.assertNotIn('title=Duke', compact.text)
        expanded = renderer.render_result(selection, 20)
        self.assertIn('title=Duke', expanded.text)

    def test_estimator_must_return_number(self):
        renderer = ContextRenderer(lambda text: 'bad')
        with self.assertRaises(TypeError):
            renderer.render(self.selection, 10)
