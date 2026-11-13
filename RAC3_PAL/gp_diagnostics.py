"""Observe varying native GP operands without asserting an object identity."""
import hashlib
import os
import struct

from .check_worker import _base_unchanged, _MEMORY


def observe(primary, references):
    """Use only complete canonical references already checked by GP recovery.

    Equal instruction/register fields at the same offsets establish a native
    operand comparison. They do not establish GP preservation through a call,
    branch, alias identity, object type, extent or runtime address.
    """
    raw = references[primary]['raw']
    groups = {}
    for offset, (word,) in enumerate(struct.iter_unpack('<I', raw)):
        if word >> 26 in _MEMORY and word >> 21 & 31 == 28:
            displacement = (word & 0x7fff) - (word & 0x8000)
            groups.setdefault(displacement, []).append(offset * 4)
    findings = []
    for displacement, offsets in groups.items():
        placements = []
        for number, reference in references.items():
            native = reference['raw']
            if len(native) != len(raw):
                break
            uses = []
            for offset in offsets:
                original = struct.unpack_from('<I', raw, offset)[0]
                word = struct.unpack_from('<I', native, offset)[0]
                if (original ^ word) & 0xffff0000:
                    break
                uses.append(dict(offset=offset, gpOffset=(word & 0x7fff) - (word & 0x8000)))
            if len(uses) != len(offsets):
                break
            placements.append(dict(program=f'level_{number:02d}', address=reference['address'],
                bodySha256=hashlib.sha256(native).hexdigest(), uses=uses,
                entryGpPrefixVerified=all(_base_unchanged(native, -4, offset, 28) for offset in offsets)))
        if len(placements) != len(references) or len(placements) < 2:
            continue
        if len({tuple(use['gpOffset'] for use in row['uses']) for row in placements}) == 1:
            continue  # Fixed SDA displacement is not an unrecovered variation.
        prefix = all(row['entryGpPrefixVerified'] for row in placements)
        findings.append(dict(primaryGpOffset=displacement, offsets=offsets, placements=placements,
            entryGpPrefixVerified=prefix,
            reason='identity-not-recovered' if prefix else 'unproven-entry-gp-prefix'))
    return findings


def warnings(evidence, language=None):
    if not evidence['findings']:
        return []
    count = len(evidence['findings'])
    german = (language or os.environ.get('PS2DECOMP_LANGUAGE', 'en')).lower().split('-')[0] == 'de'
    if german:
        return [f'RAC3 GP: {count} Originaloperand-Gruppen haben unterschiedliche GP-Offsets in den Kopien '
                'und bleiben ohne bewiesene Identität. gpIdentityDiagnostics prüfen; SDA-Ersatznamen '
                'beweisen keine Objektidentität. Quelltext und Prüfregeln bleiben unverändert.']
    return [f'RAC3 GP: {count} native operand groups have varying GP offsets across copies and remain '
            'outside identity recovery. Review gpIdentityDiagnostics; fallback SDA names do not prove '
            'object identity. Source and verification rules remain unchanged.']
