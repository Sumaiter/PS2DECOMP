"""Fresh, immutable-revision GitHub match metadata; never read C or game data.

Call fetch_matches separately at list and start time. An optional explicit
client implements get_json(endpoint); cache only saves validated TSV parsing.
All network or schema failures raise MatchMapError, including with a warm cache.
Physical overlap is conflict evidence, not proof that source names are aliases.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import datetime
import hashlib
import json
import math
import os
from pathlib import PurePosixPath
import re
import subprocess

REPO = 'OpenRAC/rac4-decomp'
DISCS = {'pal': 'SCES_532.85', 'us': 'SCUS_974.65'}
STATUSES = frozenset(('match', 'differs', 'nomatch', 'other', 'near', 'none'))
FUNCTION = re.compile(r'func_(?:L\d{2}_|FB_)?[0-9A-F]{8}\Z')
SOURCE_NAME = re.compile(r'[A-Za-z_][A-Za-z_0-9]*\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')
MAX_MAP_BYTES = 16 * 1024 * 1024


class MatchMapError(ValueError):
    """Current upstream match coverage could not be established safely."""


@dataclass(frozen=True)
class MapRow:
    name: str
    file: str
    status: str
    names: tuple[str, ...]
    places: tuple[tuple[str, str, int, int], ...]
    closeness: float

    def as_dict(self):
        return dict(name=self.name, file=self.file, status=self.status, names=list(self.names),
                    places=[list(place) for place in self.places], closeness=self.closeness)


@dataclass(frozen=True)
class MatchSnapshot:
    version: str
    revision: str
    map_hash: str
    blob_sha: str
    fetched_at: str
    rows: tuple[MapRow, ...]

    @property
    def matches(self):
        return tuple(row for row in self.rows if row.status == 'match')

    @property
    def placements(self):
        return frozenset(place for row in self.matches for place in row.places)

    @property
    def names(self):
        """Version-native catalogue names; source/other-version names remain in rows."""
        return frozenset(name for row in self.matches for name in row.names)

    @property
    def source_names(self):
        return frozenset(row.name for row in self.matches)

    def as_dict(self):
        return dict(complete=True, version=self.version, revision=self.revision, mapHash=self.map_hash,
                    blobSHA=self.blob_sha, fetchedAt=self.fetched_at,
                    matches=[row.as_dict() for row in self.matches])


@dataclass(frozen=True)
class _CachedRows:
    key: tuple[str, str, str]
    raw: bytes
    rows: tuple[MapRow, ...]


def _version(version):
    if version not in DISCS:
        raise MatchMapError('Unbekannte Spielversion für aktuelle Upstream-Matches.')


def parse_map(data: bytes, version: str) -> tuple[MapRow, ...]:
    """Validate every six-column row, even rows that will not block work."""
    _version(version)
    if not isinstance(data, bytes) or not data or len(data) > MAX_MAP_BYTES or not data.endswith(b'\n'):
        raise MatchMapError('Leere, zu große oder abgeschnittene Upstream-Matchkarte.')
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise MatchMapError('Upstream-Matchkarte ist kein UTF-8.') from exc
    if '\0' in text or not text.startswith('# Each function ') or DISCS[version] not in text.splitlines()[0]:
        raise MatchMapError('Upstream-Matchkarte gehört nicht zur angeforderten Spielversion.')
    rows, seen = [], set()
    for line_number, line in enumerate(text.splitlines(), 1):
        if line.startswith('#') or not line:
            continue
        fields = line.split('\t')
        if len(fields) != 6 or any(not field or field != field.strip() for field in fields):
            raise MatchMapError(f'Unvollständige TSV-Zeile {line_number}.')
        name, filename, status, native, placements, closeness = fields
        path = PurePosixPath(filename)
        if (not SOURCE_NAME.fullmatch(name) or name in seen or status not in STATUSES
                or '\\' in filename or path.is_absolute() or '..' in path.parts
                or path.parts[0] not in ('src', 'nonmatching') or path.suffix != '.c'
                or str(path) != filename):
            raise MatchMapError(f'Ungültige Identität, Quelldatei oder Status in Zeile {line_number}.')
        seen.add(name)
        names = () if native == '-' else tuple(native.split(','))
        if any(not FUNCTION.fullmatch(n) for n in names) or len(names) != len(set(names)):
            raise MatchMapError(f'Ungültige Katalognamen in Zeile {line_number}.')
        try:
            close = float(closeness)
        except ValueError as exc:
            raise MatchMapError(f'Ungültiger Vergleichswert in Zeile {line_number}.') from exc
        if not math.isfinite(close) or not 0 <= close <= 100:
            raise MatchMapError(f'Vergleichswert außerhalb des Bereichs in Zeile {line_number}.')
        places = []
        for placement in placements.split(',') if placements != '-' else ():
            parts = placement.split(':')
            if len(parts) != 4:
                raise MatchMapError(f'Ungültige Platzierung in Zeile {line_number}.')
            program, section, address, size = parts
            valid_sections = {'core.text', 'net.text', '.text'} if program == 'main' else {'.text', 'net.text'} if program == 'frontbin' else {'.text'}
            if (program not in ('main', 'frontbin') and not re.fullmatch(r'level_\d{2}', program)
                    or section not in valid_sections or not re.fullmatch(r'[0-9A-Fa-f]{8}', address)
                    or not re.fullmatch(r'[1-9][0-9]*', size)):
                raise MatchMapError(f'Ungültiges Programm/Segment/Intervall in Zeile {line_number}.')
            address, size = int(address, 16), int(size)
            if address % 4 or size % 4 or address == 0 or address + size > 0x100000000:
                raise MatchMapError(f'Platzierung außerhalb des 32-Bit-Codebereichs in Zeile {line_number}.')
            places.append((program, section, address, size))
        if len(places) != len(set(places)) or bool(names) != bool(places):
            raise MatchMapError(f'Unvollständige/doppelte Platzierungsbindung in Zeile {line_number}.')
        if status == 'match' and (not names or not places or close != 100):
            raise MatchMapError(f'Unvollständiger Match in Zeile {line_number}.')
        rows.append(MapRow(name, filename, status, names, tuple(places), close))
    if not rows:
        raise MatchMapError('Upstream-Matchkarte enthält keine Funktionszeilen.')
    return tuple(rows)


class GitHubClient:
    """Existing gh authentication only; no configuration or network writes."""
    def __init__(self, timeout=30):
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 45:
            raise ValueError('GitHub timeout must be bounded by 45 seconds')
        self.timeout = timeout

    def get_json(self, endpoint):
        command = ['gh', 'api', '--method', 'GET', endpoint, '--header', 'Accept: application/vnd.github+json',
                   '--header', 'X-GitHub-Api-Version: 2022-11-28', '--hostname', 'github.com']
        try:
            run = subprocess.run(command, capture_output=True, timeout=self.timeout,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MatchMapError('Aktuelle Upstream-Matches nicht erreichbar; Start angehalten.') from exc
        if run.returncode or len(run.stdout) > MAX_MAP_BYTES * 2:
            raise MatchMapError('GitHub-Matchabfrage fehlgeschlagen oder unvollständig; Start angehalten.')
        try:
            return json.loads(run.stdout.decode('utf-8'))
        except (UnicodeError, ValueError) as exc:
            raise MatchMapError('Ungültige/unvollständige GitHub-Matchantwort.') from exc


def _get(client, endpoint):
    try:
        result = client.get_json(endpoint)
    except MatchMapError:
        raise
    except Exception as exc:
        raise MatchMapError('Aktuelle Upstream-Matchprüfung fehlgeschlagen; kein Offline-Fallback.') from exc
    if not isinstance(result, dict):
        raise MatchMapError('GitHub-Matchantwort ist kein vollständiges Objekt.')
    return result


def _blob(payload, expected_sha, expected_size):
    if payload.get('sha') != expected_sha or payload.get('size') != expected_size or payload.get('encoding') != 'base64':
        raise MatchMapError('GitHub-Blobidentität, Größe oder Kodierung stimmt nicht überein.')
    content = payload.get('content')
    if not isinstance(content, str):
        raise MatchMapError('GitHub-Blobinhalt fehlt.')
    try:
        raw = base64.b64decode(content.replace('\n', ''), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MatchMapError('GitHub-Blobinhalt ist ungültig/abgeschnitten.') from exc
    actual = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
    if len(raw) != expected_size or actual != expected_sha:
        raise MatchMapError('GitHub-Blobprüfung fehlgeschlagen; keine Teilkarte übernommen.')
    return raw


def fetch_matches(version, *, client=None, cache=None):
    """Fresh main check on EVERY invocation, including when cache is populated.

    client: explicit object with get_json(endpoint), defaults to authenticated gh.
    cache: optional mapping keyed by (version, main SHA, verified map SHA256).
    Callers must invoke this again at start; no saved local snapshot is current.
    """
    _version(version)
    client = GitHubClient() if client is None else client
    head = _get(client, f'repos/{REPO}/git/ref/heads/main')
    obj = head.get('object', {})
    revision = obj.get('sha') if isinstance(obj, dict) else None
    if (head.get('ref') != 'refs/heads/main' or not isinstance(obj, dict) or obj.get('type') != 'commit'
            or not isinstance(revision, str) or not SHA.fullmatch(revision)):
        raise MatchMapError('Aktuelle main-Revision ist nicht eindeutig geprüft.')
    path = f'config/{version}/map.tsv'
    payload = _get(client, f'repos/{REPO}/contents/{path}?ref={revision}')
    blob_sha, size = payload.get('sha'), payload.get('size')
    if (payload.get('type') != 'file' or payload.get('path') != path or payload.get('name') != 'map.tsv'
            or not isinstance(blob_sha, str) or not SHA.fullmatch(blob_sha)
            or isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_MAP_BYTES):
        raise MatchMapError('Upstream-Matchdatei ist nicht vollständig revisionsgebunden.')
    # GitHub omits contents above 1MiB; use the same immutable Git blob then.
    if payload.get('encoding') == 'none' and payload.get('content') == '':
        payload = _get(client, f'repos/{REPO}/git/blobs/{blob_sha}')
    raw = _blob(payload, blob_sha, size)
    map_hash = hashlib.sha256(raw).hexdigest()
    key = (version, revision, map_hash)
    # Cache stores immutable rows and the original bytes, so altered external
    # cache entries cannot replace verified network evidence with other rows.
    cached = cache.get(key) if cache is not None else None
    if isinstance(cached, _CachedRows) and cached.key == key and cached.raw == raw:
        rows = cached.rows
    else:
        rows = parse_map(raw, version)
    if cache is not None:
        cache[key] = _CachedRows(key, raw, rows)
    return MatchSnapshot(version, revision, map_hash, blob_sha,
                         datetime.datetime.now(datetime.timezone.utc).isoformat(), rows)
