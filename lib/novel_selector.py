"""Read-only, deterministic retrieval helpers for Novel Mode.

The classes in this module deliberately know nothing about Calibre, SQLite, or
``novel.py``.  ``ContextSelector`` accepts a small duck-typed query adapter so
that a future ``novel_memory`` store remains the single owner of persistence.
"""

from __future__ import unicode_literals

from collections import defaultdict, namedtuple
from dataclasses import dataclass
from types import MappingProxyType
import re
import unicodedata


_SPACE_RE = re.compile(r'\s+')
_WORD_RE = re.compile(r'\w', re.UNICODE)
_UNSPACED_SCRIPT_RE = re.compile(
    r'[\u0e00-\u0e7f\u0e80-\u0eff\u1780-\u17ff\u1000-\u109f'
    r'\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]')

# Pronouns do not provide reliable identity resolution.  This intentionally
# conservative English set prevents the most damaging accidental aliases while
# allowing callers to add language-specific denials when they need them.
DEFAULT_PRONOUNS = frozenset((
    'i', 'me', 'my', 'mine', 'myself', 'you', 'your', 'yours', 'yourself',
    'he', 'him', 'his', 'himself', 'she', 'her', 'hers', 'herself', 'it',
    'its', 'itself', 'we', 'us', 'our', 'ours', 'ourselves', 'they', 'them',
    'their', 'theirs', 'themselves', 'this', 'that', 'these', 'those',
))

AliasMatch = namedtuple('AliasMatch', 'alias entity_ids start end')
AmbiguityResolution = namedtuple(
    'AmbiguityResolution', 'entity_ids confidence')


def normalize_alias(value):
    """Return the stable comparison form used by the alias index.

    NFKC handles compatibility forms, ``casefold`` handles Unicode case more
    completely than ``lower``, and punctuation is converted to whitespace so
    spelling variants such as ``Lord-Alexander`` and ``Lord Alexander`` agree.
    """
    if value is None:
        return ''
    value = unicodedata.normalize('NFKC', str(value)).casefold()
    value = ''.join(
        ' ' if unicodedata.category(char).startswith('P') else char
        for char in value)
    return _SPACE_RE.sub(' ', value).strip()


def _has_word_boundary(text, start, end):
    """Require a boundary at each edge without assuming ASCII text."""
    before_is_word = start > 0 and bool(_WORD_RE.match(text[start - 1]))
    after_is_word = end < len(text) and bool(_WORD_RE.match(text[end]))
    return not before_is_word and not after_is_word


def text_matches_alias(text, value):
    """Return whether ``value`` occurs in ``text`` under alias matching rules.

    Word boundaries protect space-delimited names such as ``Alex`` from
    matching ``Alexei``.  They are not valid token boundaries for scripts
    normally written without spaces, where a known term must be matchable
    inside a sentence.
    """
    value = normalize_alias(value)
    text = normalize_alias(text)
    if not value:
        return False
    require_boundaries = not _UNSPACED_SCRIPT_RE.search(value)
    start = text.find(value)
    while start >= 0:
        end = start + len(value)
        if not require_boundaries or _has_word_boundary(text, start, end):
            return True
        start = text.find(value, start + 1)
    return False


def candidate_id(kind, record_id):
    """Return a diagnostics-only ID that cannot collide across record types."""
    return '%s:%s' % (kind, str(record_id))


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze(item)
                                 for key, item in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze(item) for item in value)
    return value


def _record_id(record, prefix):
    value = record.get('entity_id') or record.get('id')
    if value is None and prefix == 'term':
        value = record.get('term_id') or record.get('source')
    return str(value) if value is not None else ''


class AliasIndex:
    """A deterministic many-to-many alias index.

    ``match`` returns every candidate for an ambiguous alias; it never picks a
    winner.  Overlapping aliases are resolved longest-first at the same text
    offset, which avoids reporting ``Alex`` in ``Alexander``.
    """

    def __init__(self, aliases=None, denied_aliases=None):
        self._aliases = defaultdict(set)
        self._denied = set(DEFAULT_PRONOUNS)
        self._denied.update(normalize_alias(item)
                            for item in (denied_aliases or ()))
        if aliases:
            if hasattr(aliases, 'items'):
                for entity_id, values in aliases.items():
                    self.add_many(entity_id, values)
            else:
                for entity_id, values in aliases:
                    self.add_many(entity_id, values)

    def add(self, entity_id, alias):
        """Add an alias, returning ``True`` when it is indexable."""
        entity_id = str(entity_id or '').strip()
        normalized = normalize_alias(alias)
        if not entity_id or not normalized or normalized in self._denied:
            return False
        self._aliases[normalized].add(entity_id)
        return True

    def add_many(self, entity_id, aliases):
        for alias in aliases or ():
            self.add(entity_id, alias)

    def candidates(self, alias):
        return tuple(sorted(self._aliases.get(normalize_alias(alias), ())))

    def __contains__(self, alias):
        return bool(self.candidates(alias))

    def match(self, text):
        """Find non-overlapping, boundary-aware alias mentions in ``text``."""
        normalized_text = normalize_alias(text)
        candidates = []
        for alias in sorted(self._aliases, key=lambda value: (-len(value), value)):
            start = normalized_text.find(alias)
            while start >= 0:
                end = start + len(alias)
                if (_UNSPACED_SCRIPT_RE.search(alias) or
                        _has_word_boundary(normalized_text, start, end)):
                    candidates.append((start, end, alias))
                start = normalized_text.find(alias, start + 1)

        # At any given start position the longest match wins.  A later match
        # that overlaps it is also suppressed, yielding stable token-like
        # matching rather than a bag of substring hits.
        matches = []
        occupied_until = -1
        for start, end, alias in sorted(candidates,
                                        key=lambda item: (item[0],
                                                          -(item[1] - item[0]),
                                                          item[2])):
            if start < occupied_until:
                continue
            matches.append(AliasMatch(
                alias, tuple(sorted(self._aliases[alias])), start, end))
            occupied_until = end
        return tuple(matches)


class ActiveEntityTracker:
    """Tracks selected entities across a bounded, decaying chunk window.

    ``load_hook`` and ``save_hook`` are optional persistence seams.  They work
    solely with the serializable output of :meth:`snapshot`; the tracker itself
    never writes to a database.
    """

    DEFAULT_DECAY = (100, 80, 60, 45, 30, 20)

    def __init__(self, window=6, decay=None, load_hook=None, save_hook=None):
        self.window = max(1, int(window))
        values = tuple(int(value) for value in (decay or self.DEFAULT_DECAY))
        if not values:
            raise ValueError('decay must contain at least one score')
        self.decay = values[:self.window]
        if len(self.decay) < self.window:
            self.decay += (self.decay[-1],) * (self.window - len(self.decay))
        self._events = []
        self._sequence = 0
        self._save_hook = save_hook
        if load_hook is not None:
            self.restore(load_hook())

    @staticmethod
    def _position_key(position):
        if position is None:
            return None
        if isinstance(position, dict):
            return (position.get('book'), position.get('chapter'),
                    position.get('chunk'))
        if isinstance(position, (tuple, list)):
            return tuple(position[:3])
        return (getattr(position, 'book', None),
                getattr(position, 'chapter', None),
                getattr(position, 'chunk', None))

    def update(self, selection, position=None):
        """Record selected entity IDs after a successful read-only selection."""
        entity_ids = getattr(selection, 'entity_ids', selection)
        self._sequence += 1
        key = self._position_key(position)
        for entity_id in sorted(set(str(item) for item in (entity_ids or ())
                                    if item)):
            self._events.append((entity_id, self._sequence, key))
        self._trim()
        if self._save_hook is not None:
            self._save_hook(self.snapshot())

    def _trim(self):
        minimum = self._sequence - self.window + 1
        self._events = [event for event in self._events if event[1] >= minimum]

    def scores(self, position=None):
        """Return ``entity_id -> (score, age)`` for active past chunks."""
        result = {}
        for entity_id, sequence, _event_position in self._events:
            age = self._sequence - sequence
            if age < 0 or age >= self.window:
                continue
            value = (self.decay[age], age)
            if entity_id not in result or value[0] > result[entity_id][0]:
                result[entity_id] = value
        return MappingProxyType(dict(sorted(result.items())))

    def snapshot(self):
        return {
            'sequence': self._sequence,
            'events': tuple({
                'entity_id': entity_id,
                'sequence': sequence,
                'position': position,
            } for entity_id, sequence, position in self._events),
        }

    def restore(self, snapshot):
        if not isinstance(snapshot, dict):
            return
        self._sequence = max(0, int(snapshot.get('sequence', 0) or 0))
        self._events = []
        for event in snapshot.get('events', ()):
            if not isinstance(event, dict) or not event.get('entity_id'):
                continue
            self._events.append((str(event['entity_id']),
                                 int(event.get('sequence', 0) or 0),
                                 self._position_key(event.get('position'))))
        self._trim()


class AmbiguityResolver:
    """Read-only optional resolver contract.

    Subclasses implement ``resolve(source_text, alias, candidate_ids,
    position)`` and return a mapping with ``entities`` entries containing only
    ``entity_id`` and optional numeric ``confidence`` values.  The selector
    validates all output and ignores invalid or out-of-candidate IDs.
    """

    def resolve(self, source_text, alias, candidate_ids, position):
        raise NotImplementedError


def validate_ambiguity_resolution(response, candidate_ids):
    """Validate a resolver response without trusting it to alter selection."""
    allowed = set(str(item) for item in candidate_ids)
    if not isinstance(response, dict) or not isinstance(response.get('entities'), list):
        return AmbiguityResolution((), MappingProxyType({}))
    confidence = {}
    for item in response['entities']:
        if not isinstance(item, dict):
            continue
        entity_id = str(item.get('entity_id') or '')
        value = item.get('confidence', 1.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if entity_id in allowed and 0.0 <= float(value) <= 1.0:
            confidence[entity_id] = max(float(value), confidence.get(entity_id, 0.0))
    entity_ids = tuple(sorted(confidence, key=lambda item: (-confidence[item], item)))
    return AmbiguityResolution(entity_ids, MappingProxyType(confidence))


@dataclass(frozen=True)
class SelectionResult:
    """Immutable output of :meth:`ContextSelector.select`.

    Records and reason maps are recursively frozen.  ``debug_reasons`` maps
    selected IDs to the exact deterministic reasons that contributed to them.
    """
    entity_ids: tuple
    term_ids: tuple
    entities: tuple
    terms: tuple
    entity_scores: object
    term_scores: object
    debug_reasons: object
    ambiguous_aliases: tuple
    story: tuple
    strategy: str


class ContextSelector:
    """Score compact context candidates from a read-only memory adapter.

    Adapter methods are optional and are never imported from or written to:
    ``get_entities(position)``, ``get_terms(position)``,
    ``get_story_context(position)``, ``get_chapter_active_entity_ids(position)``
    and ``get_related_entity_ids(entity_ids, position)``.  ``iter_*`` aliases
    are accepted as a convenience for lightweight adapters.
    """

    SCORES = {
        'exact-canonical': 100,
        'exact-alias': 95,
        'exact-glossary': 90,
        'chapter-active': 30,
        'core': 25,
        'relationship': 15,
    }

    def __init__(self, memory=None, alias_index=None, active_tracker=None,
                 ambiguity_resolver=None, scores=None):
        self.memory = memory
        self.alias_index = alias_index or AliasIndex()
        self.active_tracker = active_tracker or ActiveEntityTracker()
        self.ambiguity_resolver = ambiguity_resolver
        self.scores = dict(self.SCORES)
        if scores:
            self.scores.update(scores)

    def _query(self, names, position, *args):
        if self.memory is None:
            return ()
        for name in names:
            method = getattr(self.memory, name, None)
            if callable(method):
                try:
                    return method(*args, position=position)
                except TypeError:
                    try:
                        return method(*args, position)
                    except TypeError:
                        return method(*args)
        return ()

    def _entities(self, position):
        records = self._query(('get_entities', 'iter_entities'), position)
        if isinstance(records, dict):
            records = records.values()
        return tuple(record for record in (records or ()) if isinstance(record, dict))

    def _terms(self, position):
        records = self._query(('get_terms', 'iter_terms', 'get_glossary_terms'), position)
        if isinstance(records, dict):
            records = records.values()
        return tuple(record for record in (records or ()) if isinstance(record, dict))

    @staticmethod
    def _matches_text(text, value):
        return text_matches_alias(text, value)

    def select(self, source_text, position=None, strategy='selective'):
        strategy = str(strategy or 'selective').lower()
        if strategy not in ('selective', 'hybrid', 'full'):
            raise ValueError('strategy must be Selective, Hybrid, or Full')
        entities = self._entities(position)
        terms = self._terms(position)
        entity_records = {_record_id(record, 'entity'): record for record in entities
                          if _record_id(record, 'entity')}
        term_records = {_record_id(record, 'term'): record for record in terms
                        if _record_id(record, 'term')}
        entity_scores = defaultdict(int)
        term_scores = defaultdict(int)
        reasons = defaultdict(list)
        ambiguous = []

        def add_entity(entity_id, score, reason):
            if entity_id not in entity_records:
                return
            entity_scores[entity_id] += int(score)
            key = candidate_id('entity', entity_id)
            if reason not in reasons[key]:
                reasons[key].append(reason)

        def add_term(term_id, score, reason):
            if term_id not in term_records:
                return
            term_scores[term_id] += int(score)
            key = candidate_id('term', term_id)
            if reason not in reasons[key]:
                reasons[key].append(reason)

        for entity_id, record in entity_records.items():
            canonical = record.get('canonical_source') or record.get('source') or record.get('name')
            if self._matches_text(source_text, canonical):
                add_entity(entity_id, self.scores['exact-canonical'], 'exact-canonical')
            if record.get('core') or record.get('sticky'):
                add_entity(entity_id, self.scores['core'], 'core')

        for match in self.alias_index.match(source_text):
            if len(match.entity_ids) == 1:
                add_entity(match.entity_ids[0], self.scores['exact-alias'],
                           'exact-alias:%s' % match.alias)
                continue
            resolved = AmbiguityResolution((), MappingProxyType({}))
            if self.ambiguity_resolver is not None:
                response = self.ambiguity_resolver.resolve(
                    source_text, match.alias, match.entity_ids, position)
                resolved = validate_ambiguity_resolution(response, match.entity_ids)
            if resolved.entity_ids:
                for entity_id in resolved.entity_ids:
                    add_entity(entity_id, self.scores['exact-alias'],
                               'ambiguous-resolved:%s' % match.alias)
            else:
                ambiguous.append(match)
                for entity_id in match.entity_ids:
                    add_entity(entity_id, self.scores['exact-alias'],
                               'ambiguous-alias:%s' % match.alias)

        for term_id, record in term_records.items():
            source = record.get('source') or record.get('canonical_source') or record.get('term')
            if self._matches_text(source_text, source):
                add_term(term_id, self.scores['exact-glossary'], 'exact-glossary')
            if record.get('core') or record.get('sticky'):
                add_term(term_id, self.scores['core'], 'core')

        for entity_id, (score, age) in self.active_tracker.scores(position).items():
            add_entity(entity_id, score, 'recent-active:%d' % age)

        chapter_active = self._query(
            ('get_chapter_active_entity_ids', 'chapter_active_entity_ids'), position)
        for entity_id in chapter_active or ():
            add_entity(str(entity_id), self.scores['chapter-active'], 'chapter-active')

        related = self._query(('get_related_entity_ids', 'related_entity_ids'),
                              position, tuple(sorted(entity_scores)))
        for entity_id in related or ():
            add_entity(str(entity_id), self.scores['relationship'], 'relationship')

        if strategy == 'full':
            for entity_id in entity_records:
                add_entity(entity_id, 0, 'full-entity')
            for term_id in term_records:
                add_term(term_id, 0, 'full-term')

        ordered_entities = tuple(sorted(entity_scores,
                                        key=lambda item: (-entity_scores[item], item)))
        ordered_terms = tuple(sorted(term_scores,
                                     key=lambda item: (-term_scores[item], item)))
        debug = {
            candidate_id('entity', item): tuple(
                reasons[candidate_id('entity', item)])
            for item in ordered_entities
        }
        debug.update({
            candidate_id('term', item): tuple(
                reasons[candidate_id('term', item)])
            for item in ordered_terms
        })
        story = self._query(('get_story_context', 'story_context'), position)
        if isinstance(story, str):
            story = (story,)
        return SelectionResult(
            ordered_entities,
            ordered_terms,
            tuple(_freeze(entity_records[item]) for item in ordered_entities),
            tuple(_freeze(term_records[item]) for item in ordered_terms),
            MappingProxyType({item: entity_scores[item] for item in ordered_entities}),
            MappingProxyType({item: term_scores[item] for item in ordered_terms}),
            MappingProxyType(debug),
            tuple(ambiguous),
            tuple(_freeze(item) for item in (story or ())),
            strategy,
        )
