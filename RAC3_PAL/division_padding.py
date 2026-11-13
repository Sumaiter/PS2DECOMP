"""Read-only evidence for RAC3's existing div.s/sqrt.s padding table."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import struct

from .check_worker import _level_references


TABLE = 'config/levels/divs_nops.txt'
FUNCTION = re.compile(r'func_L(\d\d)_([0-9A-F]{8})\Z')


def _control(word):
    op, rs, fn = word >> 26, word >> 21 & 31, word & 63
    return (op in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23)
            or op == 0 and fn in (8, 9)
            or op in (17, 18) and rs == 8)


def native_sites(raw):
    """Count consecutive real NOP words, excluding control delay slots.

    This records the native stream. It does not identify a scheduling cause,
    certify an original source or insert padding into compiler output.
    """
    if not isinstance(raw, bytes) or not raw or len(raw) % 4:
        return None
    words = list(struct.unpack('<' + 'I' * (len(raw) // 4), raw))
    sites = []
    for index, word in enumerate(words):
        if (word & 0xffe0003f) not in (0x46000003, 0x46000004):
            continue
        sqrt = word & 63 == 4
        if sqrt and word >> 16 & 31:
            return None  # Unsupported nonzero unused SQRT.S operand field.
        offsets, cursor, excluded = [], index - 1, None
        while cursor >= 0 and words[cursor] == 0:
            if cursor > 0 and _control(words[cursor - 1]):
                excluded = cursor * 4
                break
            offsets.append(cursor * 4)
            cursor -= 1
        sites.append(dict(offset=index * 4, mnemonic='sqrt.s' if sqrt else 'div.s',
                          count=len(offsets), nopOffsets=list(reversed(offsets)),
                          excludedDelayNopOffset=excluded))
    return sites


def table_counts(text, name):
    """Parse the canonical table without silently accepting duplicate rows."""
    rows = {}
    for line in text.splitlines():
        fields = line.split('#', 1)[0].split()
        if not fields:
            continue
        if (len(fields) < 2 or not FUNCTION.fullmatch(fields[0])
                or fields[0] in rows or any(not re.fullmatch(r'[0-9]+', token) for token in fields[1:])):
            raise ValueError('Malformed or duplicate canonical padding row')
        rows[fields[0]] = [int(token) for token in fields[1:]]
    return rows.get(name)


def inspect(engine, name):
    """Optional decompile evidence; failures never change the rough-input path."""
    result = dict(status='not-applicable', table=TABLE, configuredCounts=None,
                  nativeCounts=None, placements=[], mutatesInputs=False,
                  changesSearchPolicy=False, semanticApproval=False)
    if not FUNCTION.fullmatch(name):
        return result
    path = engine.root / TABLE
    try:
        blob = path.read_bytes()
        result['tableSha256'] = hashlib.sha256(blob).hexdigest()
        result['configuredCounts'] = table_counts(blob.decode('utf-8'), name)
    except FileNotFoundError:
        result['status'] = 'missing-table'
        return result
    except (OSError, UnicodeError, ValueError):
        result['status'] = 'invalid-table'
        return result
    try:
        # Most functions have no relevant instruction. This is an applicability
        # prefilter only, not a statement about uninspected secondary copies.
        # Do not hash/read every program merely to add an irrelevant warning.
        preliminary = native_sites(engine.disc_bytes(engine.place(name)))
        if preliminary == [] and result['configuredCounts'] is None:
            result['scope'] = 'no-primary-operation-and-no-config-entry'
            return result
        checked = _level_references(name)
        if checked is None:
            raise ValueError('Missing canonical references')
        primary, references = checked
        match = FUNCTION.fullmatch(name)
        expected = {(f'level_{number:02d}', '.text', row['address'], len(row['raw']))
                    for number, row in references.items()}
        places = [tuple(place[:4]) for place in engine.all_places(name)]
        place = tuple(engine.place(name)[:4])
        if (primary != int(match[1]) or len(places) != len(set(places)) or set(places) != expected
                or place != (f'level_{primary:02d}', '.text', int(match[2], 16), len(references[primary]['raw']))
                or engine.disc_bytes(place) != references[primary]['raw']):
            raise ValueError('Changed placement identity')
        for number, row in references.items():
            sites = native_sites(row['raw'])
            if sites is None:
                raise ValueError('Unsupported native encoding')
            result['placements'].append(dict(program=f'level_{number:02d}', address=row['address'],
                bytes=len(row['raw']), referenceSha256=hashlib.sha256(row['raw']).hexdigest(), sites=sites))
    except Exception:
        result.update(status='unavailable-native-evidence', placements=[])
        return result
    counts = [[site['count'] for site in row['sites']] for row in result['placements']]
    if not counts or any(row != counts[0] for row in counts):
        result['status'] = 'inconsistent-native-padding'
        return result
    result['nativeCounts'] = counts[0]
    configured = result['configuredCounts']
    if configured is None:
        result['status'] = 'missing-entry' if any(counts[0]) else ('no-padding-required' if counts[0] else 'not-applicable')
    elif len(configured) != len(counts[0]):
        result['status'] = 'operation-count-mismatch'
    elif configured != counts[0]:
        result['status'] = 'padding-count-mismatch'
    else:
        result['status'] = 'configured-corroborated'
    return result


def warnings(evidence, language=None):
    """Explain only diagnostic limitations; no match or configuration approval."""
    status = evidence['status']
    if status in ('not-applicable', 'no-padding-required', 'configured-corroborated'):
        return []
    german = (language or os.environ.get('PS2DECOMP_LANGUAGE', 'en')).lower().split('-')[0] == 'de'
    if german:
        text = ('RAC3 div.s/sqrt.s-Padding: ' + status + '; Original=' + str(evidence['nativeCounts'])
                + ', Tabelle=' + str(evidence['configuredCounts'])
                + '. Nur Diagnose; Konfiguration, Quelltext und Suchbudget bleiben unverändert.')
    else:
        text = ('RAC3 div.s/sqrt.s padding: ' + status + '; native=' + str(evidence['nativeCounts'])
                + ', table=' + str(evidence['configuredCounts'])
                + '. Diagnostic only; configuration, source and search budget remain unchanged.')
    return [text]


def main():
    import argparse
    import json
    from .rac3_engine import Engine
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--owner')
    args = parser.parse_args()
    engine = Engine(args.repo)
    engine.assert_access(args.name, args.owner)
    print(json.dumps(inspect(engine, args.name), indent=2))


if __name__ == '__main__':
    main()
