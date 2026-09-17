"""Calibre-independent, append-only memory for novel translation.

Version four deliberately starts from a clean database.  Version three data is
archived rather than trusted as an input to identity or chronology decisions.
All durable observations are scoped and positioned before they can be read.
"""
from __future__ import unicode_literals

import hashlib
import json
import os
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


SCHEMA_VERSION = 4
LEGACY_MIGRATION_VERSION = 1


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), default=str)


def _unjson(value, default=None):
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _decimal(value):
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError):
        raise ValueError('book_order must be a finite non-negative decimal')
    if not result.is_finite() or result < 0:
        raise ValueError('book_order must be a finite non-negative decimal')
    return result


def _decimal_text(value):
    result = _decimal(value).normalize()
    text = format(result, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def normalize_identity(value):
    """Normalize names and aliases with one deterministic lookup contract."""
    value = unicodedata.normalize('NFKC', str(value or '')).casefold()
    value = ''.join(' ' if unicodedata.category(char)[0] in ('P', 'Z') else char
                    for char in value)
    return ' '.join(value.split())


@dataclass(frozen=True, init=False)
class StoryPosition:
    """A canonical ``(book_order, chapter, phase, chunk)`` story position.

    The historical three positional arguments remain accepted as
    ``(chapter, phase, offset)``; new code supplies ``book_order=``.  ``offset``
    remains an alias for ``chunk`` for the existing converter integration.
    """
    BEFORE_CHAPTER = 0
    IN_CHAPTER = 1
    AFTER_CHAPTER = 2

    book_order: str
    chapter: int
    phase: int
    chunk: int

    def __init__(self, chapter, phase=IN_CHAPTER, offset=0, book_order='0',
                 chunk=None):
        if chunk is not None:
            if offset not in (0, None):
                raise ValueError('use either offset or chunk')
            offset = chunk
        chapter = int(chapter)
        phase = self._phase_value(phase)
        offset = int(offset or 0)
        if chapter < 0 or offset < 0:
            raise ValueError('chapter and chunk must be non-negative')
        object.__setattr__(self, 'book_order', _decimal_text(book_order))
        object.__setattr__(self, 'chapter', chapter)
        object.__setattr__(self, 'phase', phase)
        object.__setattr__(self, 'chunk', offset)

    @property
    def offset(self):
        return self.chunk

    @classmethod
    def _phase_value(cls, value):
        if isinstance(value, str):
            values = {'before': cls.BEFORE_CHAPTER,
                      'before_chapter': cls.BEFORE_CHAPTER,
                      'in': cls.IN_CHAPTER, 'chapter': cls.IN_CHAPTER,
                      'in_chapter': cls.IN_CHAPTER,
                      'after': cls.AFTER_CHAPTER,
                      'after_chapter': cls.AFTER_CHAPTER}
            if value.lower() not in values:
                raise ValueError('unknown story position phase: %r' % value)
            return values[value.lower()]
        value = int(value)
        if value not in (cls.BEFORE_CHAPTER, cls.IN_CHAPTER, cls.AFTER_CHAPTER):
            raise ValueError('unknown story position phase: %r' % value)
        return value

    @classmethod
    def after_chapter(cls, chapter, book_order='0'):
        return cls(chapter, cls.AFTER_CHAPTER, 0, book_order)

    @classmethod
    def from_json(cls, value):
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(value['chapter'], value.get('phase', cls.IN_CHAPTER),
                       value.get('chunk', value.get('offset', 0)),
                       value.get('book_order', '0'))
        if isinstance(value, (list, tuple)):
            if len(value) == 4:
                # Persisted v4 tuple order is book, chapter, phase, chunk.
                return cls(value[1], value[2], value[3], value[0])
            return cls(*value)
        raise TypeError('position must be a StoryPosition, dict, or tuple')

    def to_json(self):
        return {'book_order': self.book_order, 'chapter': self.chapter,
                'phase': self.phase, 'chunk': self.chunk, 'offset': self.chunk}

    def key(self):
        return (_decimal(self.book_order), self.chapter, self.phase, self.chunk)

    def __lt__(self, other):
        return self.key() < StoryPosition.from_json(other).key()

    def __le__(self, other):
        return self.key() <= StoryPosition.from_json(other).key()


@dataclass(frozen=True, init=False)
class MemoryDescriptor:
    """Stable memory namespace and canonical order of its current book."""
    series_id: str
    book_id: str
    source_language: str
    target_language: str
    scope: str
    book_order: str

    def __init__(self, series_id='', book_id='', source_language='',
                 target_language='', scope='book', source_lang=None,
                 target_lang=None, book_order=0):
        if source_lang is not None:
            source_language = source_lang
        if target_lang is not None:
            target_language = target_lang
        scope = str(scope or 'book').lower()
        if scope not in ('book', 'series'):
            raise ValueError("scope must be 'book' or 'series'")
        if scope == 'book' and not str(book_id or ''):
            raise ValueError('book scope requires book_id')
        if scope == 'series' and not str(series_id or ''):
            raise ValueError('series scope requires series_id')
        object.__setattr__(self, 'series_id', str(series_id or ''))
        object.__setattr__(self, 'book_id', '' if scope == 'series' else str(book_id or ''))
        object.__setattr__(self, 'source_language', str(source_language or ''))
        object.__setattr__(self, 'target_language', str(target_language or ''))
        object.__setattr__(self, 'scope', scope)
        object.__setattr__(self, 'book_order', _decimal_text(book_order))

    @property
    def source_lang(self):
        return self.source_language

    @property
    def target_lang(self):
        return self.target_language

    @classmethod
    def book(cls, book_id, source_language='', target_language='', series_id='',
             book_order=0):
        return cls(series_id, book_id, source_language, target_language, 'book',
                   book_order=book_order)

    @classmethod
    def series(cls, series_id, source_language='', target_language='',
               book_order=0):
        return cls(series_id, '', source_language, target_language, 'series',
                   book_order=book_order)

    def to_json(self):
        return {'series_id': self.series_id, 'book_id': self.book_id,
                'source_language': self.source_language,
                'target_language': self.target_language, 'scope': self.scope,
                'book_order': self.book_order}

    to_dict = to_json

    @classmethod
    def from_json(cls, value):
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            value = json.loads(value)
        return cls(**value)

    from_dict = from_json

    def database_path(self, cache_root):
        return scope_database_path(cache_root, self.source_language,
                                   self.target_language)


def build_memory_descriptor(metadata, source_language='', target_language='',
                            series_enabled=True):
    metadata = dict(metadata or {})
    library = str(metadata.get('library_identity') or '').strip()
    book = str(metadata.get('book_uuid') or metadata.get('book_id') or '').strip()
    if not book:
        raise ValueError('book identity is required for novel memory')
    series = str(metadata.get('series_key_override') or metadata.get('series') or '').strip()
    raw_order = metadata.get('series_order_override')
    if raw_order in (None, '', 0, '0'):
        raw_order = metadata.get('series_index')
    try:
        order = _decimal_text(raw_order if raw_order not in (None, '') else 0)
    except ValueError:
        order = '0'
    book_key = '%s:%s' % (library or 'library', book)
    if series_enabled and metadata.get('series_memory_enabled', True) and series \
            and _decimal(order) > 0:
        return MemoryDescriptor.book(book_key, source_language, target_language,
                                     '%s:%s' % (library or 'library', series), order)
    return MemoryDescriptor.book(book_key, source_language, target_language,
                                 book_order=order)


def _safe_component(value):
    text = re.sub(r'[^a-z0-9]+', '-', str(value or '').strip().lower()).strip('-')
    digest = hashlib.sha256(str(value or '').encode('utf-8')).hexdigest()[:10]
    return '%s-%s' % ((text or 'unspecified')[:40], digest)


def scope_database_path(cache_root, source_language='', target_language=None):
    if isinstance(source_language, MemoryDescriptor):
        target_language = source_language.target_language
        source_language = source_language.source_language
    root = os.path.abspath(os.path.expanduser(str(cache_root)))
    directory = os.path.join(root, 'novel-memory')
    os.makedirs(directory, exist_ok=True)
    filename = '%s--%s.v4.sqlite3' % (_safe_component(source_language),
                                      _safe_component(target_language))
    path = os.path.abspath(os.path.join(directory, filename))
    if os.path.commonpath([root, path]) != root:
        raise ValueError('memory database escaped cache root')
    return path


class NovelMemoryStore:
    """Safe v4 SQLite memory store.

    A v3 file is never migrated in place.  The constructor archives it before
    creating this distinct v4 store; any database claiming a future version is
    rejected without a write.
    """
    def __init__(self, database_path, timeout=30):
        self.database_path = os.path.abspath(database_path)
        parent = os.path.dirname(self.database_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.archived_paths = []
        self._archive_sibling_v3()
        self._prepare_target_database()
        self.connection = sqlite3.connect(self.database_path, timeout=timeout)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA foreign_keys = ON')
        self._bootstrap_schema()

    @classmethod
    def open(cls, cache_root, source_language='', target_language=''):
        return cls(scope_database_path(cache_root, source_language, target_language))

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    @staticmethod
    def _schema_version(path):
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return None
        connection = sqlite3.connect(path)
        try:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if 'metadata' not in tables:
                return None
            row = connection.execute(
                'SELECT value FROM metadata WHERE key=?', ('schema_version',)).fetchone()
            return int(row[0]) if row else None
        finally:
            connection.close()

    def _archive_file(self, path, reason):
        stamp = time.strftime('%Y%m%d%H%M%S')
        target = '%s.%s.%s.archive.sqlite3' % (path, reason, stamp)
        suffix = 1
        while os.path.exists(target):
            target = '%s.%s.%s.%d.archive.sqlite3' % (path, reason, stamp, suffix)
            suffix += 1
        os.replace(path, target)
        try:
            os.chmod(target, 0o444)
        except OSError:
            pass
        self.archived_paths.append(target)
        return target

    def _archive_sibling_v3(self):
        marker = '.v4.sqlite3'
        if not self.database_path.endswith(marker):
            return
        legacy = self.database_path[:-len(marker)] + '.sqlite3'
        if os.path.exists(legacy):
            version = self._schema_version(legacy)
            if version is None or version < SCHEMA_VERSION:
                self._archive_file(legacy, 'v%s' % (version if version is not None else 'legacy'))

    def _prepare_target_database(self):
        version = self._schema_version(self.database_path)
        if version is not None and version > SCHEMA_VERSION:
            raise RuntimeError('memory schema version %s is newer than supported v%s' %
                               (version, SCHEMA_VERSION))
        if version is not None and version < SCHEMA_VERSION:
            # v1-v3 have unsafe chronology semantics.  Archive, do not stamp.
            self._archive_file(self.database_path, 'v%s' % version)

    def _bootstrap_schema(self):
        tables = {row['name'] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'metadata' in tables:
            row = self.connection.execute('SELECT value FROM metadata WHERE key=?',
                                          ('schema_version',)).fetchone()
            if row:
                version = int(row['value'])
                if version > SCHEMA_VERSION:
                    raise RuntimeError('memory schema is newer than this plugin')
                if version < SCHEMA_VERSION:
                    raise RuntimeError('legacy database escaped archive bootstrap')
                self._validate_v4_schema()
                if self.archived_paths:
                    with self.connection:
                        self.connection.execute(
                            'INSERT INTO metadata(key,value) VALUES (?,?) '
                            'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                            ('v3_archive_path', self.archived_paths[-1]))
                return
        with self.connection:
            self.connection.executescript('''
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE scopes (
                    id INTEGER PRIMARY KEY, series_id TEXT NOT NULL DEFAULT '',
                    book_id TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL,
                    book_order TEXT NOT NULL, descriptor_json TEXT NOT NULL,
                    UNIQUE(series_id, book_id, scope), UNIQUE(id, scope)
                );
                CREATE TABLE extraction_runs (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL, chapter INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(scope_id, chapter, fingerprint),
                    FOREIGN KEY(scope_id) REFERENCES scopes(id)
                );
                CREATE TABLE entities (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL,
                    canonical_name TEXT NOT NULL, normalized_canonical TEXT NOT NULL,
                    entity_type TEXT, data_json TEXT NOT NULL DEFAULT '{}',
                    book_order TEXT NOT NULL, chapter INTEGER NOT NULL, phase INTEGER NOT NULL,
                    chunk INTEGER NOT NULL, UNIQUE(scope_id, normalized_canonical),
                    UNIQUE(id, scope_id), FOREIGN KEY(scope_id) REFERENCES scopes(id)
                );
                CREATE TABLE aliases (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL, entity_id INTEGER NOT NULL,
                    alias TEXT NOT NULL, normalized_alias TEXT NOT NULL,
                    book_order TEXT NOT NULL, chapter INTEGER NOT NULL, phase INTEGER NOT NULL,
                    chunk INTEGER NOT NULL, provenance TEXT NOT NULL DEFAULT 'model',
                    UNIQUE(scope_id, entity_id, normalized_alias),
                    FOREIGN KEY(scope_id) REFERENCES scopes(id),
                    FOREIGN KEY(entity_id, scope_id) REFERENCES entities(id, scope_id)
                );
                CREATE TABLE terms (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL,
                    source_name TEXT NOT NULL, normalized_source TEXT NOT NULL,
                    term_type TEXT, data_json TEXT NOT NULL DEFAULT '{}',
                    book_order TEXT NOT NULL, chapter INTEGER NOT NULL, phase INTEGER NOT NULL,
                    chunk INTEGER NOT NULL, UNIQUE(scope_id, normalized_source),
                    UNIQUE(id, scope_id), FOREIGN KEY(scope_id) REFERENCES scopes(id)
                );
                CREATE TABLE entity_name_versions (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL, entity_id INTEGER NOT NULL,
                    source_name TEXT, target_name TEXT NOT NULL, confidence TEXT NOT NULL,
                    book_order TEXT NOT NULL, chapter INTEGER NOT NULL, phase INTEGER NOT NULL,
                    chunk INTEGER NOT NULL, extraction_run_id INTEGER,
                    FOREIGN KEY(entity_id, scope_id) REFERENCES entities(id, scope_id),
                    FOREIGN KEY(extraction_run_id) REFERENCES extraction_runs(id)
                );
                CREATE TABLE term_name_versions (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL, term_id INTEGER NOT NULL,
                    source_name TEXT, target_name TEXT NOT NULL, confidence TEXT NOT NULL,
                    book_order TEXT NOT NULL, chapter INTEGER NOT NULL, phase INTEGER NOT NULL,
                    chunk INTEGER NOT NULL, extraction_run_id INTEGER,
                    FOREIGN KEY(term_id, scope_id) REFERENCES terms(id, scope_id),
                    FOREIGN KEY(extraction_run_id) REFERENCES extraction_runs(id)
                );
                CREATE TABLE entity_facts (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL, entity_id INTEGER NOT NULL,
                    predicate TEXT NOT NULL, normalized_key TEXT NOT NULL, value_json TEXT NOT NULL,
                    confidence TEXT NOT NULL, book_order TEXT NOT NULL, chapter INTEGER NOT NULL,
                    phase INTEGER NOT NULL, chunk INTEGER NOT NULL, extraction_run_id INTEGER,
                    FOREIGN KEY(entity_id, scope_id) REFERENCES entities(id, scope_id),
                    FOREIGN KEY(extraction_run_id) REFERENCES extraction_runs(id)
                );
                CREATE TABLE entity_state_events (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL, entity_id INTEGER NOT NULL,
                    state_key TEXT NOT NULL, normalized_key TEXT NOT NULL, value_json TEXT NOT NULL,
                    confidence TEXT NOT NULL, book_order TEXT NOT NULL, chapter INTEGER NOT NULL,
                    phase INTEGER NOT NULL, chunk INTEGER NOT NULL, extraction_run_id INTEGER,
                    FOREIGN KEY(entity_id, scope_id) REFERENCES entities(id, scope_id),
                    FOREIGN KEY(extraction_run_id) REFERENCES extraction_runs(id)
                );
                CREATE TABLE summaries (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL, kind TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '', content TEXT NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}', book_order TEXT NOT NULL,
                    chapter INTEGER NOT NULL, phase INTEGER NOT NULL, chunk INTEGER NOT NULL,
                    extraction_run_id INTEGER, FOREIGN KEY(scope_id) REFERENCES scopes(id),
                    FOREIGN KEY(extraction_run_id) REFERENCES extraction_runs(id)
                );
                CREATE TABLE activity_chunks (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL, book_id TEXT NOT NULL,
                    book_order TEXT NOT NULL, chapter INTEGER NOT NULL, phase INTEGER NOT NULL,
                    chunk INTEGER NOT NULL, checkpoint TEXT NOT NULL DEFAULT '',
                    UNIQUE(scope_id, book_id, book_order, chapter, phase, chunk),
                    FOREIGN KEY(scope_id) REFERENCES scopes(id)
                );
                CREATE TABLE mentions (
                    id INTEGER PRIMARY KEY, activity_chunk_id INTEGER NOT NULL,
                    subject_kind TEXT, subject_id INTEGER, mention_text TEXT NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(activity_chunk_id, subject_kind, subject_id, mention_text),
                    FOREIGN KEY(activity_chunk_id) REFERENCES activity_chunks(id)
                );
                CREATE TABLE user_locks (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL,
                    subject_kind TEXT NOT NULL, subject_id INTEGER NOT NULL,
                    field_name TEXT NOT NULL, value_json TEXT NOT NULL,
                    archived_origin TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(scope_id) REFERENCES scopes(id)
                );
                CREATE TABLE unresolved_proposals (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL, chapter INTEGER NOT NULL,
                    kind TEXT NOT NULL, normalized_payload TEXT NOT NULL, reason TEXT NOT NULL,
                    extraction_run_id INTEGER, FOREIGN KEY(scope_id) REFERENCES scopes(id),
                    FOREIGN KEY(extraction_run_id) REFERENCES extraction_runs(id)
                );
                CREATE TABLE unresolved_user_edits (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL, archived_origin TEXT NOT NULL,
                    payload_json TEXT NOT NULL, reason TEXT NOT NULL,
                    FOREIGN KEY(scope_id) REFERENCES scopes(id)
                );
                CREATE TABLE migration_imports (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL, source TEXT NOT NULL,
                    migration_identity TEXT NOT NULL, migration_version INTEGER NOT NULL,
                    UNIQUE(scope_id, source, migration_identity, migration_version),
                    FOREIGN KEY(scope_id) REFERENCES scopes(id)
                );
                CREATE TABLE chapter_checkpoints (
                    id INTEGER PRIMARY KEY, scope_id INTEGER NOT NULL, book_id TEXT NOT NULL,
                    chapter INTEGER NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                    UNIQUE(scope_id, book_id, chapter), FOREIGN KEY(scope_id) REFERENCES scopes(id)
                );
                CREATE INDEX ix_alias_lookup ON aliases(scope_id, normalized_alias);
                CREATE INDEX ix_entity_discovery ON entities(scope_id, book_order, chapter, phase, chunk);
                CREATE INDEX ix_term_discovery ON terms(scope_id, book_order, chapter, phase, chunk);
                CREATE INDEX ix_state_key ON entity_state_events(scope_id, entity_id, normalized_key);
                CREATE INDEX ix_checkpoint ON chapter_checkpoints(scope_id, book_id, chapter);
            ''')
            self.connection.execute('INSERT INTO metadata(key,value) VALUES (?,?)',
                                    ('schema_version', str(SCHEMA_VERSION)))
            for path in self.archived_paths:
                self.connection.execute('INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)',
                                        ('v3_archive_path', path))

    def _validate_v4_schema(self):
        required = {'metadata', 'scopes', 'entities', 'aliases', 'terms',
                    'entity_state_events', 'chapter_checkpoints', 'migration_imports',
                    'unresolved_user_edits'}
        present = {row['name'] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = required - present
        if missing:
            raise RuntimeError('v4 schema is incomplete: %s' % ', '.join(sorted(missing)))

    @property
    def schema_version(self):
        return int(self.get_metadata('schema_version'))

    def get_metadata(self, key, default=None):
        row = self.connection.execute('SELECT value FROM metadata WHERE key=?',
                                      (key,)).fetchone()
        return row['value'] if row else default

    def set_metadata(self, key, value):
        with self.connection:
            self.connection.execute('INSERT INTO metadata(key,value) VALUES (?,?) '
                                    'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                    (str(key), str(value)))

    def _scope_id(self, descriptor, commit=True):
        descriptor = MemoryDescriptor.from_json(descriptor)
        row = self.connection.execute('SELECT id FROM scopes WHERE series_id=? AND book_id=? AND scope=?',
                                      (descriptor.series_id, descriptor.book_id,
                                       descriptor.scope)).fetchone()
        if row:
            return row['id']
        cursor = self.connection.execute(
            'INSERT INTO scopes(series_id,book_id,scope,book_order,descriptor_json) VALUES (?,?,?,?,?)',
            (descriptor.series_id, descriptor.book_id, descriptor.scope,
             descriptor.book_order, _json(descriptor.to_json())))
        if commit:
            self.connection.commit()
        return cursor.lastrowid

    @staticmethod
    def _position(position, descriptor=None):
        if position is None:
            order = descriptor.book_order if descriptor else '0'
            return StoryPosition(0, StoryPosition.BEFORE_CHAPTER, 0, order)
        position = StoryPosition.from_json(position)
        if descriptor is not None and position.book_order == '0' and descriptor.book_order != '0':
            position = StoryPosition(position.chapter, position.phase, position.chunk,
                                     descriptor.book_order)
        return position

    @staticmethod
    def _row_position(row):
        return StoryPosition(row['chapter'], row['phase'], row['chunk'], row['book_order'])

    @classmethod
    def _visible(cls, row, at_position):
        return at_position is None or cls._row_position(row) <= StoryPosition.from_json(at_position)

    @staticmethod
    def _event_dict(row):
        result = dict(row)
        if 'value_json' in result:
            result['value'] = _unjson(result.pop('value_json'))
        if 'data_json' in result:
            result['data'] = _unjson(result.pop('data_json'), {})
        if 'book_order' in result and 'chapter' in result:
            result['position'] = StoryPosition(result['chapter'], result['phase'],
                                               result['chunk'], result['book_order']).to_json()
            result['offset'] = result['chunk']
        return result

    @staticmethod
    def _confidence(value):
        if value not in ('inferred', 'confirmed'):
            raise ValueError('confidence must be inferred or confirmed')
        return value

    @staticmethod
    def _event_key(event):
        position = StoryPosition.from_json(event['position'])
        return (position.key(), 1 if event.get('confidence') == 'confirmed' else 0,
                _json(event.get('value', event.get('target_name', ''))), event['id'])

    def _owned_subject(self, scope_id, subject_kind, subject_id):
        table = 'entities' if subject_kind == 'entity' else 'terms'
        row = self.connection.execute('SELECT id FROM %s WHERE id=? AND scope_id=?' % table,
                                      (int(subject_id), scope_id)).fetchone()
        if not row:
            raise ValueError('%s id does not belong to this memory scope' % subject_kind)

    def _discovery_visible(self, row, position):
        return self._visible(row, position)

    def create_entity(self, descriptor, canonical_name, entity_type=None, data=None,
                      position=None, _commit=True):
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor, _commit)
        canonical_name = str(canonical_name or '').strip()
        normalized = normalize_identity(canonical_name)
        if not normalized:
            raise ValueError('canonical_name is required')
        existing = self.connection.execute(
            'SELECT id FROM entities WHERE scope_id=? AND normalized_canonical=?',
            (scope_id, normalized)).fetchone()
        if existing:
            return existing['id']
        p = self._position(position, descriptor)
        cursor = self.connection.execute(
            'INSERT INTO entities(scope_id,canonical_name,normalized_canonical,entity_type,data_json,book_order,chapter,phase,chunk) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (scope_id, canonical_name, normalized, entity_type, _json(data or {}),
             p.book_order, p.chapter, p.phase, p.chunk))
        entity_id = cursor.lastrowid
        self.add_alias(descriptor, entity_id, canonical_name, p, 'canonical', False)
        if _commit:
            self.connection.commit()
        return entity_id

    def add_alias(self, descriptor, entity_id, alias, position=None, provenance='model',
                  _commit=True):
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor, _commit)
        self._owned_subject(scope_id, 'entity', entity_id)
        alias = str(alias or '').strip()
        normalized = normalize_identity(alias)
        if not normalized:
            return None
        p = self._position(position, descriptor)
        self.connection.execute(
            'INSERT OR IGNORE INTO aliases(scope_id,entity_id,alias,normalized_alias,book_order,chapter,phase,chunk,provenance) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (scope_id, int(entity_id), alias, normalized, p.book_order, p.chapter,
             p.phase, p.chunk, str(provenance or 'model')))
        if _commit:
            self.connection.commit()
        return int(entity_id)

    def resolve_entity_candidates(self, descriptor, name, position=None):
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor)
        normalized = normalize_identity(name)
        p = self._position(position, descriptor) if position is not None else None
        rows = self.connection.execute(
            'SELECT e.* FROM entities e WHERE e.scope_id=? AND e.normalized_canonical=? ORDER BY e.id',
            (scope_id, normalized)).fetchall()
        candidates = [row for row in rows if self._discovery_visible(row, p)]
        aliases = self.connection.execute(
            'SELECT e.* FROM aliases a JOIN entities e ON e.id=a.entity_id AND e.scope_id=a.scope_id '
            'WHERE a.scope_id=? AND a.normalized_alias=? ORDER BY e.normalized_canonical,e.id',
            (scope_id, normalized)).fetchall()
        for row in aliases:
            if self._discovery_visible(row, p) and row['id'] not in {x['id'] for x in candidates}:
                # Alias discovery itself must also be eligible.
                alias = self.connection.execute('SELECT * FROM aliases WHERE scope_id=? AND entity_id=? AND normalized_alias=?',
                                                (scope_id, row['id'], normalized)).fetchone()
                if alias and self._discovery_visible(alias, p):
                    candidates.append(row)
        return tuple(sorted((row['id'] for row in candidates)))

    def find_entity(self, descriptor, name, position=None):
        candidates = self.resolve_entity_candidates(descriptor, name, position)
        return candidates[0] if len(candidates) == 1 else None

    def create_term(self, descriptor, source_name, term_type=None, data=None,
                    position=None, _commit=True):
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor, _commit)
        source_name = str(source_name or '').strip()
        normalized = normalize_identity(source_name)
        if not normalized:
            raise ValueError('source_name is required')
        existing = self.connection.execute('SELECT id FROM terms WHERE scope_id=? AND normalized_source=?',
                                           (scope_id, normalized)).fetchone()
        if existing:
            return existing['id']
        p = self._position(position, descriptor)
        cursor = self.connection.execute(
            'INSERT INTO terms(scope_id,source_name,normalized_source,term_type,data_json,book_order,chapter,phase,chunk) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (scope_id, source_name, normalized, term_type, _json(data or {}),
             p.book_order, p.chapter, p.phase, p.chunk))
        if _commit:
            self.connection.commit()
        return cursor.lastrowid

    def _append_name(self, table, descriptor, subject_id, source_name, target_name,
                     position=None, confidence='inferred', timeless=False,
                     extraction_run_id=None, _commit=True):
        if timeless:
            raise ValueError('timeless model observations are unsupported in v4')
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor, _commit)
        kind = 'entity' if table.startswith('entity') else 'term'
        self._owned_subject(scope_id, kind, subject_id)
        p = self._position(position, descriptor)
        confidence = self._confidence(confidence)
        column = 'entity_id' if kind == 'entity' else 'term_id'
        cursor = self.connection.execute(
            'INSERT INTO %s(scope_id,%s,source_name,target_name,confidence,book_order,chapter,phase,chunk,extraction_run_id) '
            'VALUES (?,?,?,?,?,?,?,?,?,?)' % (table, column),
            (scope_id, int(subject_id), str(source_name or ''), str(target_name), confidence,
             p.book_order, p.chapter, p.phase, p.chunk, extraction_run_id))
        if _commit:
            self.connection.commit()
        return cursor.lastrowid

    def append_entity_name_version(self, descriptor, entity_id, source_name, target_name,
                                   position=None, confidence='inferred', timeless=False,
                                   _commit=True, **kwargs):
        return self._append_name('entity_name_versions', descriptor, entity_id,
                                 source_name, target_name, position, confidence, timeless,
                                 kwargs.get('extraction_run_id'), _commit)

    def append_term_name_version(self, descriptor, term_id, source_name, target_name,
                                 position=None, confidence='inferred', timeless=False,
                                 _commit=True, **kwargs):
        return self._append_name('term_name_versions', descriptor, term_id,
                                 source_name, target_name, position, confidence, timeless,
                                 kwargs.get('extraction_run_id'), _commit)

    write_entity_name = append_entity_name_version
    write_term_name = append_term_name_version

    def append_entity_fact(self, descriptor, entity_id, predicate, value, position=None,
                           confidence='inferred', timeless=False, extraction_run_id=None,
                           _commit=True):
        if timeless:
            raise ValueError('timeless model observations are unsupported in v4')
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor, _commit)
        self._owned_subject(scope_id, 'entity', entity_id)
        predicate = str(predicate or '').strip()
        key = normalize_identity(predicate)
        if not key:
            raise ValueError('fact predicate is required')
        p = self._position(position, descriptor)
        cursor = self.connection.execute(
            'INSERT INTO entity_facts(scope_id,entity_id,predicate,normalized_key,value_json,confidence,book_order,chapter,phase,chunk,extraction_run_id) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (scope_id, int(entity_id), predicate, key, _json(value), self._confidence(confidence),
             p.book_order, p.chapter, p.phase, p.chunk, extraction_run_id))
        if _commit:
            self.connection.commit()
        return cursor.lastrowid

    def append_entity_state_event(self, descriptor, entity_id, state, value=None,
                                  position=None, confidence='inferred', timeless=False,
                                  extraction_run_id=None, _commit=True):
        """Append one event per state key; mapping input preserves old callers."""
        # v3 callers used ``(state_mapping, position, confidence)``.  Preserve
        # that form while allowing v4's explicit ``(key, value, position=)``.
        if isinstance(value, StoryPosition) or isinstance(value, (tuple, list)) or \
                (isinstance(value, dict) and 'chapter' in value):
            if isinstance(position, str):
                confidence = position
            position, value = value, None
        if timeless:
            raise ValueError('timeless model observations are unsupported in v4')
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor, _commit)
        self._owned_subject(scope_id, 'entity', entity_id)
        if isinstance(state, dict) and value is None:
            pairs = sorted(state.items(), key=lambda item: normalize_identity(item[0]))
        else:
            pairs = [(state, value)]
        p = self._position(position, descriptor)
        ids = []
        for key, item_value in pairs:
            key = str(key or '').strip()
            normalized = normalize_identity(key)
            if not normalized:
                raise ValueError('state key is required')
            cursor = self.connection.execute(
                'INSERT INTO entity_state_events(scope_id,entity_id,state_key,normalized_key,value_json,confidence,book_order,chapter,phase,chunk,extraction_run_id) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (scope_id, int(entity_id), key, normalized, _json(item_value),
                 self._confidence(confidence), p.book_order, p.chapter, p.phase,
                 p.chunk, extraction_run_id))
            ids.append(cursor.lastrowid)
        if _commit:
            self.connection.commit()
        return ids[0] if len(ids) == 1 else ids

    append_state_event = append_entity_state_event

    def append_user_lock(self, descriptor, subject_kind, subject_id, field_name, value,
                         _commit=True, archived_origin=None):
        if subject_kind not in ('entity', 'term'):
            raise ValueError("subject_kind must be 'entity' or 'term'")
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor, _commit)
        self._owned_subject(scope_id, subject_kind, subject_id)
        cursor = self.connection.execute(
            'INSERT INTO user_locks(scope_id,subject_kind,subject_id,field_name,value_json,archived_origin) VALUES (?,?,?,?,?,?)',
            (scope_id, subject_kind, int(subject_id), str(field_name), _json(value),
             archived_origin))
        if _commit:
            self.connection.commit()
        return cursor.lastrowid

    def lock_entity_name(self, descriptor, entity_id, target_name, source_name=None):
        return self.append_user_lock(descriptor, 'entity', entity_id, 'name',
                                     {'source_name': source_name, 'target_name': target_name})

    def lock_term_name(self, descriptor, term_id, target_name, source_name=None):
        return self.append_user_lock(descriptor, 'term', term_id, 'name',
                                     {'source_name': source_name, 'target_name': target_name})

    def lock_fact(self, descriptor, entity_id, predicate, value):
        return self.append_user_lock(descriptor, 'entity', entity_id,
                                     'fact:%s' % normalize_identity(predicate),
                                     {'predicate': str(predicate), 'value': value})

    def _latest_lock(self, scope_id, kind, subject_id, field):
        return self.connection.execute(
            'SELECT * FROM user_locks WHERE scope_id=? AND subject_kind=? AND subject_id=? AND field_name=? ORDER BY id DESC LIMIT 1',
            (scope_id, kind, int(subject_id), field)).fetchone()

    def _effective_name(self, descriptor, kind, subject_id, at_position):
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor)
        self._owned_subject(scope_id, kind, subject_id)
        lock = self._latest_lock(scope_id, kind, subject_id, 'name')
        if lock:
            value = _unjson(lock['value_json'], {}) or {}
            return {'id': lock['id'], 'source_name': value.get('source_name'),
                    'target_name': value.get('target_name'), 'value': value,
                    'confidence': 'user_locked', 'position': None}
        table = 'entity_name_versions' if kind == 'entity' else 'term_name_versions'
        column = 'entity_id' if kind == 'entity' else 'term_id'
        rows = self.connection.execute('SELECT * FROM %s WHERE scope_id=? AND %s=?' %
                                       (table, column), (scope_id, int(subject_id))).fetchall()
        events = [self._event_dict(row) for row in rows if self._visible(row, at_position)]
        return max(events, key=self._event_key) if events else None

    def effective_entity_name(self, descriptor, entity_id, at_position=None):
        return self._effective_name(descriptor, 'entity', entity_id, at_position)

    def effective_term_name(self, descriptor, term_id, at_position=None):
        return self._effective_name(descriptor, 'term', term_id, at_position)

    resolve_entity_name = effective_entity_name
    resolve_term_name = effective_term_name

    def effective_entity(self, descriptor, entity_id, at_position=None):
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor)
        row = self.connection.execute('SELECT * FROM entities WHERE id=? AND scope_id=?',
                                      (int(entity_id), scope_id)).fetchone()
        if not row or not self._visible(row, at_position):
            return None
        result = self._event_dict(row)
        result['name'] = self.effective_entity_name(descriptor, entity_id, at_position)
        facts = {}
        for raw in self.connection.execute('SELECT * FROM entity_facts WHERE scope_id=? AND entity_id=?',
                                           (scope_id, int(entity_id))):
            if self._visible(raw, at_position):
                event = self._event_dict(raw)
                key = raw['normalized_key']
                if key not in facts or self._event_key(event) > self._event_key(facts[key]):
                    facts[key] = event
        for lock in self.connection.execute(
                'SELECT * FROM user_locks WHERE scope_id=? AND subject_kind=? AND subject_id=? AND field_name LIKE ?',
                (scope_id, 'entity', int(entity_id), 'fact:%')):
            value = _unjson(lock['value_json'], {}) or {}
            key = normalize_identity(value.get('predicate'))
            if key:
                facts[key] = {'id': lock['id'], 'predicate': value.get('predicate'),
                              'value': value.get('value'), 'confidence': 'user_locked',
                              'position': None}
        result['facts'] = [facts[key] for key in sorted(facts)]
        states = {}
        for raw in self.connection.execute('SELECT * FROM entity_state_events WHERE scope_id=? AND entity_id=?',
                                           (scope_id, int(entity_id))):
            if self._visible(raw, at_position):
                event = self._event_dict(raw)
                key = raw['normalized_key']
                if key not in states or self._event_key(event) > self._event_key(states[key]):
                    states[key] = event
        result['states'] = [states[key] for key in sorted(states)]
        result['state'] = {'value': {event['state_key']: event['value']
                                     for event in result['states']}}
        return result

    get_effective_entity = effective_entity

    def append_summary(self, descriptor, content, position=None, title='', kind='chapter',
                       data=None, extraction_run_id=None, _commit=True):
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor, _commit)
        p = self._position(position, descriptor)
        cursor = self.connection.execute(
            'INSERT INTO summaries(scope_id,kind,title,content,data_json,book_order,chapter,phase,chunk,extraction_run_id) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (scope_id, str(kind), str(title or ''), str(content or ''), _json(data or {}),
             p.book_order, p.chapter, p.phase, p.chunk, extraction_run_id))
        if _commit:
            self.connection.commit()
        return cursor.lastrowid

    write_summary = append_summary

    def get_summaries(self, descriptor, at_position=None, kind=None):
        scope_id = self._scope_id(descriptor)
        sql, params = 'SELECT * FROM summaries WHERE scope_id=?', [scope_id]
        if kind is not None:
            sql += ' AND kind=?'
            params.append(kind)
        rows = self.connection.execute(sql + ' ORDER BY id', params).fetchall()
        return [self._event_dict(row) for row in rows if self._visible(row, at_position)]

    def _activity_chunk(self, descriptor, position, checkpoint='', _commit=True):
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor, _commit)
        p = self._position(position, descriptor)
        row = self.connection.execute(
            'SELECT id FROM activity_chunks WHERE scope_id=? AND book_id=? AND book_order=? AND chapter=? AND phase=? AND chunk=?',
            (scope_id, descriptor.book_id, p.book_order, p.chapter, p.phase, p.chunk)).fetchone()
        if row:
            return row['id']
        cursor = self.connection.execute(
            'INSERT INTO activity_chunks(scope_id,book_id,book_order,chapter,phase,chunk,checkpoint) VALUES (?,?,?,?,?,?,?)',
            (scope_id, descriptor.book_id, p.book_order, p.chapter, p.phase, p.chunk,
             str(checkpoint or '')))
        return cursor.lastrowid

    def write_activity_mention(self, descriptor, mention_text, position, subject_kind=None,
                               subject_id=None, data=None, checkpoint=None, _commit=True):
        if subject_kind not in (None, 'entity', 'term'):
            raise ValueError('invalid activity subject kind')
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor, _commit)
        if subject_kind and subject_id is not None:
            self._owned_subject(scope_id, subject_kind, subject_id)
        chunk_id = self._activity_chunk(descriptor, position, checkpoint or '', False)
        self.connection.execute(
            'INSERT OR IGNORE INTO mentions(activity_chunk_id,subject_kind,subject_id,mention_text,data_json) VALUES (?,?,?,?,?)',
            (chunk_id, subject_kind, subject_id, str(mention_text or ''), _json(data or {})))
        if _commit:
            self.connection.commit()
        row = self.connection.execute(
            'SELECT id FROM mentions WHERE activity_chunk_id=? AND subject_kind IS ? AND subject_id IS ? AND mention_text=?',
            (chunk_id, subject_kind, subject_id, str(mention_text or ''))).fetchone()
        return row['id']

    append_mention = write_activity_mention
    record_activity_mention = write_activity_mention

    def record_chunk_activity(self, descriptor, position, entity_ids, checkpoint=None,
                              mention_text=None, _commit=True):
        """Idempotently persist only entities resolved from this source chunk."""
        ids = []
        for entity_id in sorted({int(value) for value in entity_ids or ()}):
            ids.append(self.write_activity_mention(descriptor, mention_text or str(entity_id),
                                                   position, 'entity', entity_id,
                                                   checkpoint=checkpoint, _commit=False))
        if _commit:
            self.connection.commit()
        return ids

    def read_activity_mentions(self, descriptor, at_position=None, subject_kind=None,
                               subject_id=None):
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor)
        sql = ('SELECT m.*,a.book_order,a.chapter,a.phase,a.chunk,a.book_id,a.checkpoint '
               'FROM mentions m JOIN activity_chunks a ON a.id=m.activity_chunk_id '
               'WHERE a.scope_id=?')
        params = [scope_id]
        if subject_kind is not None:
            sql += ' AND m.subject_kind=?'
            params.append(subject_kind)
        if subject_id is not None:
            sql += ' AND m.subject_id=?'
            params.append(int(subject_id))
        rows = self.connection.execute(sql + ' ORDER BY a.chapter,a.phase,a.chunk,m.id', params).fetchall()
        return [self._event_dict(row) for row in rows if self._visible(row, at_position)]

    get_activity_mentions = read_activity_mentions

    def write_end_of_book_snapshot(self, descriptor, content, data=None):
        descriptor = MemoryDescriptor.from_json(descriptor)
        position = StoryPosition.after_chapter(10 ** 9, descriptor.book_order)
        payload = {'content': str(content or ''), 'data': data or {}}
        latest = self.get_end_of_book_snapshot(descriptor)
        if latest and _json({'content': latest['content'], 'data': latest.get('data', {})}) == _json(payload):
            return latest['id']
        return self.append_summary(descriptor, content, position, kind='end_of_book', data=data)

    def get_end_of_book_snapshot(self, descriptor):
        snapshots = self.get_summaries(descriptor, kind='end_of_book')
        return snapshots[-1] if snapshots else None

    read_end_of_book_snapshot = get_end_of_book_snapshot

    def get_prior_book_snapshots(self, descriptor, at_position=None):
        descriptor = MemoryDescriptor.from_json(descriptor)
        if not descriptor.series_id:
            return []
        requested = self._position(at_position, descriptor) if at_position else StoryPosition(
            10 ** 9, StoryPosition.AFTER_CHAPTER, 0, descriptor.book_order)
        rows = self.connection.execute(
            'SELECT s.book_id,s.book_order AS scope_book_order,u.* FROM scopes s JOIN summaries u ON u.scope_id=s.id '
            'WHERE s.scope=? AND s.series_id=? AND s.book_id<>? AND u.kind=? ORDER BY s.book_id,u.id',
            ('book', descriptor.series_id, descriptor.book_id, 'end_of_book')).fetchall()
        result = []
        for row in rows:
            scope_position = StoryPosition(row['chapter'], row['phase'], row['chunk'],
                                           row['book_order'])
            if _decimal(row['scope_book_order']) < _decimal(requested.book_order) and scope_position <= requested:
                event = self._event_dict(row)
                event['book_id'] = row['book_id']
                result.append(event)
        return sorted(result, key=lambda item: (_decimal(item['position']['book_order']), item['id']))

    prior_book_snapshots = get_prior_book_snapshots

    def export_snapshot(self, descriptor):
        """Return a portable, JSON-safe end-of-book memory snapshot."""
        descriptor = MemoryDescriptor.from_json(descriptor)
        snapshot = self.get_end_of_book_snapshot(descriptor)
        if not snapshot:
            raise ValueError('no end-of-book memory snapshot is available')
        return {
            'format': 'ebook-translator-novel-memory',
            'version': 1,
            'source_language': descriptor.source_language,
            'target_language': descriptor.target_language,
            'series_id': descriptor.series_id,
            'book_order': descriptor.book_order,
            'content': snapshot.get('content', ''),
            'data': snapshot.get('data', {}),
        }

    def import_snapshot(self, descriptor, payload):
        """Append a validated portable snapshot without replacing user locks."""
        descriptor = MemoryDescriptor.from_json(descriptor)
        if not isinstance(payload, dict) or payload.get('format') != \
                'ebook-translator-novel-memory' or payload.get('version') != 1:
            raise ValueError('unsupported memory snapshot file')
        if (payload.get('source_language') != descriptor.source_language or
                payload.get('target_language') != descriptor.target_language):
            raise ValueError('snapshot language pair does not match this book')
        data = payload.get('data') or {}
        if not isinstance(data, dict):
            raise ValueError('invalid memory snapshot data')
        position = StoryPosition.after_chapter(0, descriptor.book_order)
        result = {'entities': 0, 'terms': 0}
        with self.connection:
            for item in data.get('entities', ()) or ():
                if not isinstance(item, dict) or not item.get('canonical_source'):
                    continue
                entity_id = self.create_entity(
                    descriptor, item['canonical_source'], item.get('type'),
                    _commit=False)
                target = item.get('canonical_target')
                if target:
                    self.append_entity_name_version(
                        descriptor, entity_id, item['canonical_source'], target,
                        position, 'confirmed', False, _commit=False)
                for alias in item.get('aliases', ()) or ():
                    self.add_alias(descriptor, entity_id, alias, position,
                                   _commit=False)
                result['entities'] += 1
            for item in data.get('terms', ()) or ():
                if not isinstance(item, dict) or not item.get('source') or \
                        not item.get('target'):
                    continue
                term_id = self.create_term(descriptor, item['source'],
                                           item.get('type'), _commit=False)
                self.append_term_name_version(
                    descriptor, term_id, item['source'], item['target'],
                    position, 'confirmed', False, _commit=False)
                result['terms'] += 1
        return result

    def _fingerprint(self, extraction, fingerprint=None):
        return str(fingerprint or hashlib.sha256(_json(extraction).encode('utf-8')).hexdigest())

    @staticmethod
    def _forbidden_model_fields(item):
        forbidden = {'timeless', 'user_locked', 'core', 'id', 'scope_id', 'lock', 'lock_scope',
                     'record_id', 'entity_id', 'term_id'}
        found = set()
        if isinstance(item, dict):
            for key, value in item.items():
                if key in forbidden:
                    found.add(key)
                found.update(NovelMemoryStore._forbidden_model_fields(value))
        elif isinstance(item, (list, tuple)):
            for value in item:
                found.update(NovelMemoryStore._forbidden_model_fields(value))
        return found

    def _reject(self, scope_id, chapter, kind, payload, reason, run_id):
        self.connection.execute(
            'INSERT INTO unresolved_proposals(scope_id,chapter,kind,normalized_payload,reason,extraction_run_id) VALUES (?,?,?,?,?,?)',
            (scope_id, int(chapter), kind, _json(payload), reason, run_id))

    def _resolve_model_entity(self, descriptor, source, position):
        scope_id = self._scope_id(descriptor, False)
        normalized = normalize_identity(source)
        canonical = self.connection.execute(
            'SELECT * FROM entities WHERE scope_id=? AND normalized_canonical=? ORDER BY id',
            (scope_id, normalized)).fetchall()
        visible = [row for row in canonical if self._visible(row, position)]
        if len(visible) == 1:
            return visible[0]['id'], None
        aliases = self.resolve_entity_candidates(descriptor, source, position)
        if len(aliases) == 1:
            return aliases[0], None
        if len(aliases) > 1:
            return None, 'ambiguous_identity'
        if canonical:
            return None, 'future_identity'
        return None, None

    def merge_chapter(self, descriptor, chapter, extraction, fingerprint=None):
        """Validate and append a chapter extraction in one transaction.

        Invalid siblings are audited and skipped.  Valid model records can only
        use inferred/confirmed confidence and never choose IDs, locks, scopes,
        timelessness, or user-only flags.
        """
        descriptor = MemoryDescriptor.from_json(descriptor)
        extraction = dict(extraction or {})
        fingerprint = self._fingerprint(extraction, fingerprint)
        position = StoryPosition.after_chapter(chapter, descriptor.book_order)
        accepted = rejected = unresolved = 0
        with self.connection:
            scope_id = self._scope_id(descriptor, False)
            existing = self.connection.execute(
                'SELECT id FROM extraction_runs WHERE scope_id=? AND chapter=? AND fingerprint=?',
                (scope_id, int(chapter), fingerprint)).fetchone()
            if existing:
                return {'merged': False, 'run_id': existing['id'], 'fingerprint': fingerprint,
                        'accepted': 0, 'rejected': 0, 'unresolved': 0}
            run_id = self.connection.execute(
                'INSERT INTO extraction_runs(scope_id,chapter,fingerprint) VALUES (?,?,?)',
                (scope_id, int(chapter), fingerprint)).lastrowid
            summary = extraction.get('summary')
            if isinstance(summary, dict):
                self.append_summary(descriptor, summary.get('content', summary.get('summary', '')),
                                    position, summary.get('title', ''), summary.get('kind', 'chapter'),
                                    summary.get('data'), run_id, False)
            elif summary:
                self.append_summary(descriptor, summary, position, extraction.get('title', ''),
                                    'chapter', None, run_id, False)
            entities = sorted((item for item in extraction.get('entities', ()) if isinstance(item, dict)),
                              key=lambda item: (normalize_identity(item.get('source_name') or item.get('source') or item.get('name')), _json(item)))
            resolved = {}
            for item in entities:
                source = str(item.get('source_name') or item.get('source') or item.get('name') or '').strip()
                bad = self._forbidden_model_fields(item)
                if bad or not normalize_identity(source):
                    self._reject(scope_id, chapter, 'entity', item, 'unsupported_field' if bad else 'invalid_shape', run_id)
                    rejected += 1
                    continue
                confidence = item.get('confidence', 'inferred')
                if confidence not in ('inferred', 'confirmed'):
                    self._reject(scope_id, chapter, 'entity', item, 'invalid_confidence', run_id)
                    rejected += 1
                    continue
                entity_id, reason = self._resolve_model_entity(descriptor, source, position)
                if reason:
                    self._reject(scope_id, chapter, 'entity', item, reason, run_id)
                    unresolved += 1
                    continue
                if entity_id is None:
                    entity_id = self.create_entity(descriptor, item.get('canonical_name', source),
                                                   item.get('type'), item.get('data'), position, False)
                resolved[normalize_identity(source)] = entity_id
                for alias in sorted({str(value).strip() for value in item.get('aliases', ()) if str(value).strip()},
                                    key=normalize_identity):
                    self.add_alias(descriptor, entity_id, alias, position, 'model', False)
                target = item.get('target_name', item.get('target', item.get('translation')))
                if target:
                    self.append_entity_name_version(descriptor, entity_id, source, target, position,
                                                    confidence, False, False, extraction_run_id=run_id)
                for fact in sorted((fact for fact in item.get('facts', ()) if isinstance(fact, dict)),
                                   key=lambda fact: (normalize_identity(fact.get('predicate') or fact.get('key')), _json(fact))):
                    if self._forbidden_model_fields(fact) or not (fact.get('predicate') or fact.get('key')):
                        self._reject(scope_id, chapter, 'fact', fact, 'unsupported_field', run_id)
                        rejected += 1
                        continue
                    fact_confidence = fact.get('confidence', confidence)
                    if fact_confidence not in ('inferred', 'confirmed'):
                        self._reject(scope_id, chapter, 'fact', fact, 'invalid_confidence', run_id)
                        rejected += 1
                        continue
                    self.append_entity_fact(descriptor, entity_id, fact.get('predicate', fact.get('key')),
                                            fact.get('value'), position, fact_confidence, False, run_id, False)
                    accepted += 1
                state = item.get('state')
                if isinstance(state, dict):
                    self.append_entity_state_event(descriptor, entity_id, state, position=position,
                                                   confidence=confidence, extraction_run_id=run_id, _commit=False)
                    accepted += len(state)
                accepted += 1
            for item in sorted((item for item in extraction.get('terms', extraction.get('glossary', ())) if isinstance(item, dict)),
                               key=lambda item: (normalize_identity(item.get('source_name') or item.get('source') or item.get('name')), _json(item))):
                source = str(item.get('source_name') or item.get('source') or item.get('name') or '').strip()
                target = item.get('target_name', item.get('target', item.get('translation')))
                bad = self._forbidden_model_fields(item)
                if bad or not normalize_identity(source) or not target:
                    self._reject(scope_id, chapter, 'term', item, 'unsupported_field' if bad else 'invalid_shape', run_id)
                    rejected += 1
                    continue
                confidence = item.get('confidence', 'inferred')
                if confidence not in ('inferred', 'confirmed'):
                    self._reject(scope_id, chapter, 'term', item, 'invalid_confidence', run_id)
                    rejected += 1
                    continue
                term_id = self.create_term(descriptor, source, item.get('type'), item.get('data'), position, False)
                self.append_term_name_version(descriptor, term_id, source, target, position,
                                              confidence, False, False, extraction_run_id=run_id)
                accepted += 1
            for item in sorted((item for item in extraction.get('facts', ()) if isinstance(item, dict)), key=_json):
                source = str(item.get('entity') or item.get('source_name') or '').strip()
                entity_id = resolved.get(normalize_identity(source))
                if entity_id is None:
                    entity_id, reason = self._resolve_model_entity(descriptor, source, position)
                else:
                    reason = None
                if self._forbidden_model_fields(item) or not entity_id or not item.get('key'):
                    self._reject(scope_id, chapter, 'fact', item, reason or 'invalid_shape', run_id)
                    unresolved += 1
                    continue
                confidence = item.get('confidence', 'inferred')
                if confidence not in ('inferred', 'confirmed'):
                    self._reject(scope_id, chapter, 'fact', item, 'invalid_confidence', run_id)
                    rejected += 1
                    continue
                self.append_entity_fact(descriptor, entity_id, item['key'], item.get('value'), position,
                                        confidence, False, run_id, False)
                accepted += 1
            for item in sorted((item for item in extraction.get('state_changes', ()) if isinstance(item, dict)), key=_json):
                source = str(item.get('entity') or item.get('source_name') or '').strip()
                entity_id = resolved.get(normalize_identity(source))
                if entity_id is None:
                    entity_id, reason = self._resolve_model_entity(descriptor, source, position)
                else:
                    reason = None
                if self._forbidden_model_fields(item) or not entity_id or not item.get('key'):
                    self._reject(scope_id, chapter, 'state', item, reason or 'invalid_shape', run_id)
                    unresolved += 1
                    continue
                confidence = item.get('confidence', 'inferred')
                if confidence not in ('inferred', 'confirmed'):
                    self._reject(scope_id, chapter, 'state', item, 'invalid_confidence', run_id)
                    rejected += 1
                    continue
                self.append_entity_state_event(descriptor, entity_id, item['key'], item.get('value'), position,
                                               confidence, False, run_id, False)
                accepted += 1
            for mention in extraction.get('mentions', ()) or ():
                if isinstance(mention, dict):
                    kind, subject_id = mention.get('subject_kind'), mention.get('subject_id')
                    if kind and subject_id is not None:
                        try:
                            self._owned_subject(scope_id, kind, subject_id)
                        except (TypeError, ValueError):
                            self._reject(scope_id, chapter, 'mention', mention, 'wrong_scope', run_id)
                            rejected += 1
                            continue
                    self.write_activity_mention(descriptor,
                                                mention.get('text', mention.get('mention_text', '')),
                                                position, kind, subject_id, mention.get('data'),
                                                _commit=False)
                elif mention:
                    self.write_activity_mention(descriptor, mention, position, _commit=False)
            self.record_chapter_checkpoint(descriptor, chapter, fingerprint, 'memory_committed', False)
        return {'merged': True, 'run_id': run_id, 'fingerprint': fingerprint,
                'accepted': accepted, 'rejected': rejected, 'unresolved': unresolved}

    def list_unresolved_proposals(self, descriptor, chapter=None):
        scope_id = self._scope_id(descriptor)
        sql, params = 'SELECT * FROM unresolved_proposals WHERE scope_id=?', [scope_id]
        if chapter is not None:
            sql += ' AND chapter=?'
            params.append(int(chapter))
        return [dict(row) for row in self.connection.execute(sql + ' ORDER BY id', params)]

    def record_chapter_checkpoint(self, descriptor, chapter, fingerprint, status='memory_committed',
                                  _commit=True):
        if status not in ('in_progress', 'memory_committed', 'failed'):
            raise ValueError('invalid chapter checkpoint status')
        descriptor = MemoryDescriptor.from_json(descriptor)
        scope_id = self._scope_id(descriptor, _commit)
        self.connection.execute(
            'INSERT INTO chapter_checkpoints(scope_id,book_id,chapter,fingerprint,status) VALUES (?,?,?,?,?) '
            'ON CONFLICT(scope_id,book_id,chapter) DO UPDATE SET fingerprint=excluded.fingerprint,status=excluded.status',
            (scope_id, descriptor.book_id, int(chapter), str(fingerprint), status))
        if _commit:
            self.connection.commit()

    def get_chapter_checkpoint(self, descriptor, chapter):
        scope_id = self._scope_id(descriptor)
        row = self.connection.execute('SELECT * FROM chapter_checkpoints WHERE scope_id=? AND book_id=? AND chapter=?',
                                      (scope_id, MemoryDescriptor.from_json(descriptor).book_id,
                                       int(chapter))).fetchone()
        return dict(row) if row else None

    def highest_contiguous_checkpoint(self, descriptor):
        scope_id = self._scope_id(descriptor)
        book_id = MemoryDescriptor.from_json(descriptor).book_id
        rows = self.connection.execute(
            'SELECT chapter FROM chapter_checkpoints WHERE scope_id=? AND book_id=? AND status=? ORDER BY chapter',
            (scope_id, book_id, 'memory_committed')).fetchall()
        expected = 1
        for row in rows:
            if row['chapter'] != expected:
                break
            expected += 1
        return expected - 1

    @staticmethod
    def _cache_read(cache_like, key):
        if hasattr(cache_like, 'get_info'):
            return cache_like.get_info(key)
        if hasattr(cache_like, 'info'):
            return getattr(cache_like, 'info').get(key)
        return cache_like.get(key) if hasattr(cache_like, 'get') else None

    @staticmethod
    def _cache_write(cache_like, key, value):
        if hasattr(cache_like, 'set_info'):
            cache_like.set_info(key, value)
        elif hasattr(cache_like, 'info'):
            getattr(cache_like, 'info')[key] = value
        elif hasattr(cache_like, '__setitem__'):
            cache_like[key] = value

    def reconcile_cache_progress(self, cache_like, descriptor):
        raw = self._cache_read(cache_like, 'novel_progress')
        try:
            cache_progress = int(_unjson(raw, raw) or 0)
        except (TypeError, ValueError):
            cache_progress = 0
        committed = self.highest_contiguous_checkpoint(descriptor)
        effective = min(cache_progress, committed) if cache_progress > committed else committed
        repaired = cache_progress != effective
        if repaired:
            self._cache_write(cache_like, 'novel_progress', effective)
        return {'cache_progress': cache_progress, 'committed_progress': committed,
                'effective_progress': effective, 'repaired': repaired}

    reconcile_checkpoints = reconcile_cache_progress

    def _legacy_identity(self, cache_like, explicit=None):
        if explicit:
            return str(explicit)
        for name in ('memory_migration_identity', 'cache_identity', 'identity', 'file_path', 'path', 'dir_path'):
            value = getattr(cache_like, name, None)
            if value:
                return 'cache:%s' % value
        if hasattr(cache_like, 'get_identity'):
            value = cache_like.get_identity()
            if value:
                return 'cache:%s' % value
        saved = self._cache_read(cache_like, 'novel_memory_migration_identity')
        if saved:
            return str(saved)
        # Cache adapters supporting persistence receive a stable generated ID.
        identity = 'adapter:%s.%s' % (cache_like.__class__.__module__, cache_like.__class__.__name__)
        self._cache_write(cache_like, 'novel_memory_migration_identity', identity)
        return identity

    def import_archived_user_locks(self, descriptor):
        """Import only explicit v3 user locks, leaving unknown subjects queued.

        Model-derived v3 rows are intentionally never read.  This runs once per
        archive path and scope and records the source path on every imported
        lock for the audit UI.
        """
        descriptor = MemoryDescriptor.from_json(descriptor)
        archive = self.get_metadata('v3_archive_path')
        result = {'imported': 0, 'unresolved': 0}
        if not archive or not os.path.exists(archive):
            return result
        with self.connection:
            scope_id = self._scope_id(descriptor, False)
            try:
                self.connection.execute(
                    'INSERT INTO migration_imports(scope_id,source,migration_identity,migration_version) VALUES (?,?,?,?)',
                    (scope_id, 'v3_user_locks', archive, LEGACY_MIGRATION_VERSION))
            except sqlite3.IntegrityError:
                return result
            legacy = sqlite3.connect(archive)
            legacy.row_factory = sqlite3.Row
            try:
                tables = {row['name'] for row in legacy.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'scopes', 'user_locks', 'entities', 'terms'}.issubset(tables):
                    return result
                old_scope = legacy.execute(
                    'SELECT id FROM scopes WHERE series_id=? AND book_id=? AND scope=?',
                    (descriptor.series_id, descriptor.book_id, descriptor.scope)).fetchone()
                if not old_scope:
                    return result
                locks = legacy.execute('SELECT * FROM user_locks WHERE scope_id=? ORDER BY id',
                                       (old_scope['id'],)).fetchall()
                for lock in locks:
                    kind = lock['subject_kind']
                    if kind not in ('entity', 'term'):
                        self.connection.execute(
                            'INSERT INTO unresolved_user_edits(scope_id,archived_origin,payload_json,reason) VALUES (?,?,?,?)',
                            (scope_id, archive, _json(dict(lock)), 'unsupported_subject'))
                        result['unresolved'] += 1
                        continue
                    table, name_column = ('entities', 'canonical_name') if kind == 'entity' else ('terms', 'source_name')
                    subject = legacy.execute('SELECT %s FROM %s WHERE id=? AND scope_id=?' %
                                             (name_column, table),
                                             (lock['subject_id'], old_scope['id'])).fetchone()
                    if not subject or not normalize_identity(subject[name_column]):
                        self.connection.execute(
                            'INSERT INTO unresolved_user_edits(scope_id,archived_origin,payload_json,reason) VALUES (?,?,?,?)',
                            (scope_id, archive, _json(dict(lock)), 'missing_subject'))
                        result['unresolved'] += 1
                        continue
                    if kind == 'entity':
                        subject_id = self.create_entity(descriptor, subject[name_column],
                                                        position=StoryPosition(0, book_order=descriptor.book_order),
                                                        _commit=False)
                    else:
                        subject_id = self.create_term(descriptor, subject[name_column],
                                                      position=StoryPosition(0, book_order=descriptor.book_order),
                                                      _commit=False)
                    self.append_user_lock(descriptor, kind, subject_id, lock['field_name'],
                                          _unjson(lock['value_json'], lock['value_json']),
                                          _commit=False, archived_origin=archive)
                    result['imported'] += 1
            finally:
                legacy.close()
        return result

    def migrate_pr590(self, cache_like, descriptor, migration_identity=None):
        """One-time legacy import keyed by immutable cache identity, not payload."""
        descriptor = MemoryDescriptor.from_json(descriptor)
        self.import_archived_user_locks(descriptor)
        def read(key):
            value = self._cache_read(cache_like, key)
            return _unjson(value, value) if isinstance(value, str) else value
        summaries, glossary = read('novel_summaries') or [], read('novel_glossary') or {}
        progress = read('novel_progress') or 0
        try:
            legacy_position = StoryPosition.after_chapter(int(progress), descriptor.book_order)
        except (TypeError, ValueError):
            legacy_position = StoryPosition.after_chapter(0, descriptor.book_order)
        identity = self._legacy_identity(cache_like, migration_identity)
        result = {'summaries': 0, 'terms': 0, 'imported': False}
        with self.connection:
            scope_id = self._scope_id(descriptor, False)
            try:
                self.connection.execute(
                    'INSERT INTO migration_imports(scope_id,source,migration_identity,migration_version) VALUES (?,?,?,?)',
                    (scope_id, 'pr590', identity, LEGACY_MIGRATION_VERSION))
            except sqlite3.IntegrityError:
                return result
            for item in summaries if isinstance(summaries, list) else ():
                if not isinstance(item, dict):
                    continue
                try:
                    position = StoryPosition.after_chapter(int(item.get('chapter', 0) or 0),
                                                           descriptor.book_order)
                except (TypeError, ValueError):
                    continue
                self.append_summary(descriptor, item.get('summary', ''), position,
                                    item.get('title', ''), 'chapter', item, _commit=False)
                result['summaries'] += 1
            for source, item in sorted(glossary.items()) if isinstance(glossary, dict) else ():
                item = item if isinstance(item, dict) else {'translation': item}
                target = item.get('translation') or item.get('target_name')
                if not normalize_identity(source) or not target:
                    continue
                term_id = self.create_term(descriptor, source, item.get('type'), item,
                                           legacy_position, False)
                self.append_term_name_version(descriptor, term_id, source, target,
                                              legacy_position, 'confirmed', False, False)
                result['terms'] += 1
            result['imported'] = True
        return result

    migrate_pr590_legacy = migrate_pr590


class MemoryQueryAdapter:
    """Read-only store view retained for the existing selector integration."""
    def __init__(self, store, descriptor):
        self.store = store
        self.descriptor = MemoryDescriptor.from_json(descriptor)

    def _scope_id(self):
        return self.store._scope_id(self.descriptor)

    def get_entities(self, position=None):
        scope_id = self._scope_id()
        rows = self.store.connection.execute('SELECT * FROM entities WHERE scope_id=? ORDER BY normalized_canonical,id',
                                             (scope_id,)).fetchall()
        result = []
        for row in rows:
            entity = self.store.effective_entity(self.descriptor, row['id'], position)
            if not entity:
                continue
            aliases = self.store.connection.execute('SELECT * FROM aliases WHERE scope_id=? AND entity_id=? ORDER BY normalized_alias,id',
                                                    (scope_id, row['id'])).fetchall()
            result.append({'entity_id': str(row['id']), 'canonical_source': row['canonical_name'],
                           'canonical_target': (entity.get('name') or {}).get('target_name', ''),
                           'type': row['entity_type'] or '',
                           'aliases': tuple(alias['alias'] for alias in aliases
                                            if self.store._visible(alias, position)),
                           'facts': {fact['predicate']: fact['value'] for fact in entity['facts']},
                           'current_state': (entity.get('state') or {}).get('value', {}),
                           'core': bool((entity.get('data') or {}).get('core'))})
        return result

    def get_terms(self, position=None):
        scope_id = self._scope_id()
        rows = self.store.connection.execute('SELECT * FROM terms WHERE scope_id=? ORDER BY normalized_source,id',
                                             (scope_id,)).fetchall()
        result = []
        for row in rows:
            if not self.store._visible(row, position):
                continue
            version = self.store.effective_term_name(self.descriptor, row['id'], position)
            if version:
                result.append({'term_id': str(row['id']), 'source': row['source_name'],
                               'target': version.get('target_name', ''),
                               'type': row['term_type'] or '',
                               'core': bool((_unjson(row['data_json'], {}) or {}).get('core'))})
        return result

    def get_aliases(self, position=None):
        return {record['entity_id']: record['aliases'] for record in self.get_entities(position)}

    def get_story_context(self, position=None):
        rows = self.store.get_summaries(self.descriptor, position, 'chapter')
        return tuple({'title': row.get('title', ''), 'summary': row.get('content', '')}
                     for row in rows[-6:])

    def get_chapter_active_entity_ids(self, position=None):
        if position is None:
            return ()
        position = StoryPosition.from_json(position)
        return tuple(sorted({str(row['subject_id']) for row in self.store.read_activity_mentions(
            self.descriptor, position, 'entity') if row['chapter'] == position.chapter
            and row.get('subject_id') is not None}))

    def get_related_entity_ids(self, entity_ids, position=None):
        related = set()
        for entity_id in entity_ids or ():
            entity = self.store.effective_entity(self.descriptor, int(entity_id), position)
            for fact in (entity or {}).get('facts', ()):
                value = fact.get('value')
                if fact.get('predicate') == 'relationship' and isinstance(value, dict) and value.get('entity_id'):
                    related.add(str(value['entity_id']))
        return tuple(sorted(related))

    def activity_snapshot(self, position=None, window=6):
        rows = self.store.read_activity_mentions(self.descriptor, position, 'entity')[-max(1, int(window)):]
        return {'sequence': len(rows), 'events': tuple({'entity_id': str(row['subject_id']),
                'sequence': index, 'position': row.get('position')} for index, row in enumerate(rows, 1)
                if row.get('subject_id') is not None)}


class MemoryMerger:
    """Compatibility facade around the store's central model validator."""
    def __init__(self, store, descriptor):
        self.store = store
        self.descriptor = MemoryDescriptor.from_json(descriptor)

    def merge(self, chapter, title, summary, proposals):
        extraction = dict(proposals or {})
        extraction['title'] = title
        extraction['summary'] = summary
        return self.store.merge_chapter(self.descriptor, chapter, extraction)


MemoryStore = NovelMemoryStore
SQLiteMemoryStore = NovelMemoryStore
