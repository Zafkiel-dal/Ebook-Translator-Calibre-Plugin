"""Compact, read-only rendering for selected Novel Mode memory."""

from __future__ import unicode_literals

from collections.abc import Mapping
from dataclasses import dataclass

from .novel_selector import candidate_id


@dataclass(frozen=True)
class RenderResult:
    """Immutable renderer diagnostics; every included line fit atomically."""
    text: str
    tokens: int
    included: tuple
    skipped: tuple
    strategy: str


class ContextRenderer:
    """Render a :class:`SelectionResult` under a supplied token estimator.

    ``token_estimator`` is either a callable accepting text or an object with
    ``estimate(text)``.  Selective uses only selector candidates; Hybrid also
    permits story context; Full receives the selector's complete record set.
    No rendering path writes to memory.
    """

    SECTION_ORDER = ('story', 'entities', 'terms')
    HEADERS = {
        'story': '[STORY CONTEXT]',
        'entities': '[RELEVANT CHARACTERS]',
        'terms': '[RELEVANT TERMS]',
    }

    def __init__(self, token_estimator):
        if callable(token_estimator):
            self._estimate = token_estimator
        elif callable(getattr(token_estimator, 'estimate', None)):
            self._estimate = token_estimator.estimate
        else:
            raise TypeError('token_estimator must be callable or expose estimate()')

    def _tokens(self, text):
        value = self._estimate(text)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError('token estimator must return a number')
        return max(0, int(value))

    @staticmethod
    def _value(record, *names):
        for name in names:
            value = record.get(name)
            if value not in (None, ''):
                return str(value)
        return ''

    def _entity_line(self, record):
        source = self._value(record, 'canonical_source', 'source', 'name')
        target = self._value(record, 'canonical_target', 'target', 'translation')
        kind = self._value(record, 'type')
        return ' | '.join(part for part in (source, target, kind) if part)

    def _entity_detail_lines(self, record):
        """Render mutable details separately from the atomic identity line."""
        source = self._value(record, 'canonical_source', 'source', 'name')
        facts = record.get('facts') or {}
        lines = []
        if isinstance(facts, Mapping):
            lines.extend('%s: %s=%s' % (source, key, facts[key])
                         for key in sorted(facts) if facts[key] not in (None, ''))
        elif isinstance(facts, (list, tuple)):
            lines.extend('%s: %s' % (source, item) for item in facts if item)
        state = record.get('current_state') or record.get('state')
        if isinstance(state, Mapping):
            lines.extend('%s: %s=%s' % (source, key, state[key])
                         for key in sorted(state) if state[key] not in (None, ''))
        elif state not in (None, ''):
            lines.append('%s: %s' % (source, state))
        return tuple(lines)

    def _term_line(self, record):
        source = self._value(record, 'source', 'canonical_source', 'term')
        target = self._value(record, 'target', 'translation', 'canonical_target')
        return '%s -> %s' % (source, target) if source and target else source or target

    @staticmethod
    def _story_line(item):
        if isinstance(item, dict):
            title = item.get('title') or ''
            text = item.get('summary') or item.get('text') or ''
            return ('%s: %s' % (title, text)).strip(': ') if title else str(text)
        return str(item)

    def _assemble(self, blocks):
        sections = []
        for section in self.SECTION_ORDER:
            lines = blocks[section]
            if lines:
                sections.append(self.HEADERS[section] + '\n' + '\n'.join(lines))
        return '\n\n'.join(sections)

    def render_result(self, selection, token_budget, strategy=None):
        strategy = str(strategy or selection.strategy or 'selective').lower()
        if strategy not in ('selective', 'hybrid', 'full'):
            raise ValueError('strategy must be Selective, Hybrid, or Full')
        budget = max(0, int(token_budget))
        blocks = {section: [] for section in self.SECTION_ORDER}
        candidates = []

        # Exact terms are first, then exact entities (which carry facts/state),
        # then active/other entities, and lastly narrative summary lines.
        for index, record in enumerate(selection.terms):
            term_id = selection.term_ids[index]
            key = candidate_id('term', term_id)
            line = self._term_line(record)
            reason = selection.debug_reasons.get(key, ())
            priority = 0 if 'exact-glossary' in reason else 2
            candidates.append((priority, -selection.term_scores[term_id], key, key,
                                'terms', line, None))
        for index, record in enumerate(selection.entities):
            entity_id = selection.entity_ids[index]
            key = candidate_id('entity', entity_id)
            reason = selection.debug_reasons.get(key, ())
            priority = 1 if ('exact-canonical' in reason or
                              any(item.startswith('exact-alias') or
                                  item.startswith('ambiguous-') for item in reason)) else 3
            candidates.append((priority, -selection.entity_scores[entity_id], key, key,
                                'entities', self._entity_line(record), None))
            for detail_index, line in enumerate(self._entity_detail_lines(record)):
                candidates.append((priority + 1, -selection.entity_scores[entity_id],
                                   '%s:detail:%d' % (key, detail_index), key,
                                   'entities', line, key))
        if strategy != 'selective':
            for index, item in enumerate(selection.story):
                candidates.append((4, index, 'story:%d' % index, 'story:%d' % index,
                                   'story', self._story_line(item), None))

        included = []
        skipped = []
        included_identities = set()
        for _priority, _score, item_id, _reason_key, section, line, identity in sorted(candidates):
            if not line:
                skipped.append(item_id)
                continue
            if identity is not None and identity not in included_identities:
                skipped.append(item_id)
                continue
            proposed = {name: list(values) for name, values in blocks.items()}
            proposed[section].append(line)
            text = self._assemble(proposed)
            if self._tokens(text) <= budget:
                blocks = proposed
                included.append(item_id)
                if identity is None and section == 'entities':
                    included_identities.add(item_id)
            else:
                skipped.append(item_id)
        text = self._assemble(blocks)
        return RenderResult(text, self._tokens(text), tuple(included),
                            tuple(skipped), strategy)

    def render(self, selection, token_budget, strategy=None):
        """Return prompt-ready text. Use :meth:`render_result` for diagnostics."""
        return self.render_result(selection, token_budget, strategy).text
