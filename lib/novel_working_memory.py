"""Ephemeral, chapter-local terminology continuity.

This module deliberately has no SQLite dependency. It carries model-proposed
terms only between chunks in the same chapter and is discarded afterwards.
Persistent MemoryStore writes remain chapter-end, append-only operations.
"""

from __future__ import unicode_literals

from collections import OrderedDict

from .novel_selector import DEFAULT_PRONOUNS, normalize_alias, text_matches_alias


class ChapterWorkingMemory:
    """First-write-wins provisional terms for the current chapter only."""

    MAX_TERMS = 8
    MAX_SOURCE_LENGTH = 80
    MAX_TARGET_LENGTH = 160

    def __init__(self):
        self._terms = OrderedDict()
        self._previous_source = ''

    def clear(self):
        self._terms.clear()
        self._previous_source = ''

    def observe_source(self, source_text):
        """Record successfully translated source for first-introduction checks."""
        source_text = str(source_text or '').strip()
        if source_text:
            self._previous_source = '%s\n%s' % (
                self._previous_source, source_text) if self._previous_source else source_text

    def add_terms(self, proposals, source_text):
        """Accept only source terms visibly introduced in this chunk.

        Terms never write to the persistent store here. Existing working
        terms are intentionally retained so a later model response cannot
        silently change an earlier chapter-local translation choice.
        """
        source_text = str(source_text or '')
        accepted = []
        for proposal in (proposals or ())[:self.MAX_TERMS]:
            if len(self._terms) >= self.MAX_TERMS:
                break
            if not isinstance(proposal, dict):
                continue
            raw_source = proposal.get('source')
            raw_target = proposal.get('target') or proposal.get('translation')
            if not isinstance(raw_source, str) or not isinstance(raw_target, str):
                continue
            source = raw_source.strip()
            target = raw_target.strip()
            normalized = normalize_alias(source)
            if (not source or not target or not normalized or
                    normalized in DEFAULT_PRONOUNS or
                    len(source) > self.MAX_SOURCE_LENGTH or
                    len(target) > self.MAX_TARGET_LENGTH or
                    '\n' in source or '\r' in source or
                    '\n' in target or '\r' in target):
                continue
            if (not text_matches_alias(source_text, source) or
                    text_matches_alias(self._previous_source, source) or
                    normalized in self._terms):
                continue
            self._terms[normalized] = {
                'term_id': 'working:%s' % normalized,
                'source': source,
                'target': target,
                'type': 'chapter-working',
                'provisional': True,
            }
            accepted.append(self._terms[normalized])
        return tuple(accepted)

    def terms(self):
        return tuple(self._terms.values())


class WorkingMemoryAdapter:
    """Overlay chapter-local terms on the read-only persistent query view."""

    def __init__(self, persistent, working):
        self.persistent = persistent
        self.working = working

    def get_terms(self, position=None):
        persisted = list(self.persistent.get_terms(position))
        existing = {normalize_alias(item.get('source')) for item in persisted}
        persisted.extend(item for item in self.working.terms()
                         if normalize_alias(item.get('source')) not in existing)
        return tuple(persisted)

    def __getattr__(self, name):
        return getattr(self.persistent, name)
