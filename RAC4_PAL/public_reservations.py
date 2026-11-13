"""Version-scoped issue-112 coordination, independent of compiler/match evidence.

Reads are deliberately fresh and fail closed. This adapter never writes comments.
"""
from __future__ import annotations

from contextlib import contextmanager
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

REPO = 'OpenRAC/rac4-decomp'
ISSUE = 112
ACCOUNT = ''  # No account inherits permission from the original workbench.
COMMENT = None
START = '<!-- rac4-werkbank:auto-reservations:start -->'
END = '<!-- rac4-werkbank:auto-reservations:end -->'
FUNCTION = re.compile(r'\bfunc_(?:L\d{2}_)?[0-9a-fA-F]{8}\b')
VERSION = {'pal': 'PAL (SCES_532.85)', 'us': 'NTSC-U (SCUS_974.65)'}


class ReservationError(ValueError):
    """Public scope is unknown, conflicted or could not be safely changed."""


def _version_words(text):
    result = set()
    if re.search(r'\bPAL\b|SCES[_ .]532[._]85', text, re.I):
        result.add('pal')
    if re.search(r'\bNTSC[- _]U\b|SCUS[_ .]974[._]65', text, re.I):
        result.add('us')
    return result


def _status(text, fallback='ambiguous'):
    text = text.lower()
    if re.search(r'not\s+(?:released|merged)|remain\w*\s+reserved|until\s+(?:merged|integrat)', text):
        return 'verified' if re.search(r'verif|matched|fertig', text) else 'active'
    if re.search(r'\b(?:released|dropped|freigegeben|integrated|merged)\b', text):
        return 'released'
    if re.search(r'verif|matched|lokal fertig|locally complete', text):
        return 'verified'
    if re.search(r'reserved|active|follow.up|pending review|reserviert', text):
        return 'active'
    return fallback


def parse_comments(comments):
    """Conservatively parse human scopes; ambiguous entries stay visible/blocked.

    Explicit per-section versions override the comment preamble. Narrative PAL
    mapping notes below an NTSC-U list do not change that list's version.
    """
    if not isinstance(comments, list):
        raise ReservationError('Unvollständige Reservierungsantwort.')
    entries = []
    for comment in comments:
        if not isinstance(comment, dict) or not isinstance(comment.get('body'), str) or not comment.get('id') or not isinstance(comment.get('user', {}).get('login'), str):
            raise ReservationError('Ungültiger Reservierungskommentar; Start angehalten.')
        body = comment['body']
        first = FUNCTION.search(body)
        preamble = body[:first.start()] if first else body
        versions = _version_words(preamble)
        default_versions = versions or {None}
        current_versions = default_versions
        state = _status(preamble)
        default_state = state
        unparsed = []
        for line in body.splitlines():
            names = FUNCTION.findall(line)
            heading = bool(re.match(r'^\s*(?:#{1,6}\s|\*\*|Version\s*:|Status\s*:|Reserved\s*[:(])', line, re.I))
            if heading and not names:
                line_versions = _version_words(line)
                if line_versions:
                    current_versions = line_versions
                elif re.match(r'^\s*(?:#{1,6}\s*)?Version\s*:', line, re.I):
                    current_versions = {None}
                state = _status(line, state if 'version' in line.lower() else default_state)
            # Only lists, fences and explicit scope/status lines are authoritative.
            # Narrative repetitions are ignored; otherwise an old follow-up recap
            # could accidentally revive a function listed in a Released section.
            stripped = line.strip()
            list_line = bool(re.match(r'^(?:[-*+]\s|\d+[.)]\s|func_|`?func_)', stripped))
            if not names:
                continue
            line_versions = _version_words(line)
            actual_versions = line_versions or current_versions
            actual_state = _status(line, state)
            authoritative = list_line or heading or re.match(r'^(?:Reserved|Released|Freigegeben)\s*:', stripped, re.I)
            if not authoritative:
                # Unknown human formats cannot silently become a free scope.
                unparsed.extend((name, version, line) for name in names for version in actual_versions)
                continue
            for name in names:
                for version in sorted(actual_versions, key=lambda x: x or ''):
                    entries.append({'name': name[:name.rfind('_') + 1] + name[name.rfind('_') + 1:].upper(),
                                    'version': version, 'status': actual_state,
                                    'owner': comment['user']['login'], 'commentId': comment['id'],
                                    'url': comment.get('html_url', ''),
                                    'line': line})
        explicit = {(e['name'], e['version']) for e in entries if e['commentId'] == comment['id']}
        for name, version, line in unparsed:
            name = name[:name.rfind('_') + 1] + name[name.rfind('_') + 1:].upper()
            if (name, version) not in explicit:
                entries.append({'name': name, 'version': version, 'status': 'ambiguous',
                                'owner': comment['user']['login'], 'commentId': comment['id'],
                                'url': comment.get('html_url', ''), 'line': line})
    unresolved = [x for x in entries if x['version'] is None or x['status'] == 'ambiguous']
    return {'complete': True, 'fetchedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'comments': comments, 'entries': entries, 'unresolved': unresolved}


def _api(method, endpoint, body=None, headers=None):
    if method != "GET" or body is not None:
        raise ReservationError("PS2DECOMP public coordination is read-only.")
    command = ['gh', 'api', '--include', '--method', method, endpoint,
               '--header', 'Accept: application/vnd.github+json',
               '--header', 'X-GitHub-Api-Version: 2022-11-28']
    for key, value in (headers or {}).items():
        command += ['--header', f'{key}: {value}']
    if body is not None:
        command += ['--input', '-']
    try:
        result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                                capture_output=True, text=True, encoding='utf-8',
                                timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReservationError('GitHub-Reservierungen nicht erreichbar; kein freier Start möglich.') from exc
    if result.returncode:
        raise ReservationError('GitHub-Reservierungen konnten nicht vollständig geprüft oder aktualisiert werden: ' + result.stderr.strip()[:500] + ' ' + result.stdout[-800:])
    try:
        header_text, payload = re.split(r'\r?\n\r?\n', result.stdout, maxsplit=1)
        if not re.match(r'HTTP/\S+\s+2\d\d\b', header_text):
            raise ValueError('HTTP status')
        response_headers = {}
        for line in header_text.splitlines()[1:]:
            if ':' in line:
                key, value = line.split(':', 1)
                response_headers[key.lower()] = value.strip()
        return json.loads(payload), response_headers
    except (ValueError, TypeError) as exc:
        raise ReservationError('Ungültige/unvollständige GitHub-Antwort.') from exc


def fetch_reservations(root=None, *, api=None):
    """Fetch every page. Failed pages raise instead of returning a partial scope."""
    api = api or _api
    endpoint = f'repos/{REPO}/issues/{ISSUE}/comments?per_page=100&page=1'
    comments = []
    seen_pages = set()
    while endpoint:
        if endpoint in seen_pages:
            raise ReservationError('Zyklische GitHub-Seitenantwort.')
        seen_pages.add(endpoint)
        page, headers = api('GET', endpoint)
        if not isinstance(page, list) or len(page) > 100:
            raise ReservationError('Unvollständige Kommentar-Seite.')
        comments.extend(page)
        links = headers.get('link', '')
        next_match = re.search(r'<([^>]+)>;\s*rel="next"', links)
        if next_match:
            url = next_match.group(1)
            prefix = f'https://api.github.com/repos/{REPO}/issues/{ISSUE}/comments?'
            if not url.startswith(prefix):
                raise ReservationError('Unerwartetes GitHub-Seitenziel.')
            endpoint = url.removeprefix('https://api.github.com/')
        elif len(page) == 100 and not links:
            # A missing pagination header cannot silently truncate a full page.
            raise ReservationError('GitHub-Seitenverweis fehlt; Reservierungen ungeklärt.')
        else:
            endpoint = None
    ids = [x.get('id') for x in comments if isinstance(x, dict)]
    if len(ids) != len(set(ids)):
        raise ReservationError('Doppelte Kommentar-Seiten; Reservierungen ungeklärt.')
    return parse_comments(comments)


def equivalents(name, aliases=None):
    """Expand explicit conflict/identity groups; never infer from numeric names."""
    group = {name}
    changed = True
    while changed:
        changed = False
        for key, values in (aliases or {}).items():
            scope = {key, *(values if not isinstance(values, str) else [values])}
            if group & scope and not scope <= group:
                group |= scope
                changed = True
    return group


def conflicts(snapshot, name, version, *, allow_own_pending=False, aliases=None):
    if version not in VERSION or snapshot.get('complete') is not True:
        raise ReservationError('Spielversion oder Reservierungsprüfung ungeklärt.')
    group = equivalents(name, aliases)
    return [entry for entry in snapshot['entries']
            if entry['name'] in group and entry['version'] in (None, version)
            and entry['status'] != 'released'
            and not (allow_own_pending and entry['owner'].casefold() == ACCOUNT.casefold()
                     and entry['commentId'] == COMMENT and entry['status'] == 'active'
                     and entry['version'] == version)]


def blocked_for_version(snapshot, version, *, include_own=True, aliases=None):
    if version not in VERSION or snapshot.get('complete') is not True:
        raise ReservationError('Spielversion oder Reservierungsprüfung ungeklärt.')
    result = set()
    for entry in snapshot['entries']:
        if entry['version'] in (None, version) and entry['status'] != 'released' and (include_own or entry['owner'].casefold() != ACCOUNT.casefold()):
            result |= equivalents(entry['name'], aliases)
    return result
