"""Name moving level GP memory operands before m2c infers a C draft.

This is operand identity evidence, not an object, type or ownership proof.
The existing checker still has to resolve the compiler's real relocations.
"""
from __future__ import annotations

import hashlib
import re
import struct

from .check_worker import _base_unchanged, _in_data, _level_references, _MEMORY


_LINE = re.compile(r'(?m)^/\* ([0-9A-F]{8}) ([0-9A-F]{8}) \*/[^\n]*$')
_OPERAND = re.compile(r'(\$\w+,\s*)(-?(?:0x[0-9A-Fa-f]+|\d+))\(\$gp\)\s*\Z')
_OPS = {32: 'lb', 33: 'lh', 35: 'lw', 36: 'lbu', 37: 'lhu', 39: 'lwu',
        40: 'sb', 41: 'sh', 43: 'sw', 49: 'lwc1', 57: 'swc1'}
_GPRS = ('zero', 'at', 'v0', 'v1', 'a0', 'a1', 'a2', 'a3',
         't0', 't1', 't2', 't3', 't4', 't5', 't6', 't7',
         's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7',
         't8', 't9', 'k0', 'k1', 'gp', 'sp', 'fp', 'ra')


def _possible_absolute_alias(raw, target, width):
    """Reject an independent absolute address identity conservatively.

    No constant propagation or object extent is guessed: either common LUI
    high-half convention could form this address, even if the native value
    later turns out to be unrelated. Zero-base immediate addresses also stay
    outside recovery. A false positive only retains the original rough input.
    """
    highs = {target >> 16, ((target + 0x8000) >> 16) & 0xffff}
    for offset in range(0, len(raw), 4):
        word = struct.unpack_from('<I', raw, offset)[0]
        op, rs = word >> 26, word >> 21 & 31
        if op == 15 and rs == 0 and (word & 0xffff) in highs:
            return True
        if op in (8, 9, 13, 25) and rs == 0:
            immediate = word & 0xffff if op == 13 else (word & 0x7fff) - (word & 0x8000)
            if target <= (immediate & 0xffffffff) < target + width:
                return True
    return False


def normalize(engine, name, assembly):
    """Return an unchanged listing on missing, incomplete or ambiguous evidence.

    Fixed GP slots keep the adapter's existing SDA handling. Only a direct
    memory identity with changing absolute targets AND changing GP slots can
    receive a level name. In particular a resident object seen through two
    different GPs must not become a moving level object.
    """
    try:
        return _normalize(engine, name, assembly)
    except Exception:
        # An optional recovery cannot replace the normal fail-closed checker.
        return assembly, []


def _normalize(engine, name, assembly):
    match = re.fullmatch(r'func_L(\d\d)_([0-9A-F]{8})', name)
    if not match or '$gp' not in assembly or name in engine.remnants:
        return assembly, []
    checked = _level_references(name)
    if checked is None:
        return assembly, []
    primary, references = checked
    if primary != int(match[1]) or len(references) < 2:
        return assembly, []
    place = tuple(engine.place(name)[:4])
    raw = references[primary]['raw']
    expected = {(f'level_{number:02d}', '.text', ref['address'], len(ref['raw']))
                for number, ref in references.items()}
    places = [tuple(p[:4]) for p in engine.all_places(name)]
    if (place != (f'level_{primary:02d}', '.text', int(match[2], 16), len(raw))
            or len(places) != len(set(places)) or set(places) != expected
            or engine.disc_bytes(place) != raw):
        return assembly, []
    lines = list(_LINE.finditer(assembly))
    if (len(lines) * 4 != len(raw) or any(
            int(line[1], 16) != place[2] + offset
            or int(line[2], 16) != struct.unpack_from('<I', raw, offset)[0]
            for offset, line in zip(range(0, len(raw), 4), lines))):
        return assembly, []

    # Address formation, GP copies and unsupported GP memory widths in ANY
    # copy could expose another identity. Do not split possible aliases.
    for ref in references.values():
        for offset in range(0, len(ref['raw']), 4):
            word = struct.unpack_from('<I', ref['raw'], offset)[0]
            op, rs, rt = word >> 26, word >> 21 & 31, word >> 16 & 31
            if (rs == 28 and op not in _MEMORY
                    or op == 0 and rt == 28
                    or op in _MEMORY and rs == 28 and rt == 28 and op not in (49, 57)):
                return assembly, []
    groups = {}
    for offset in range(0, len(raw), 4):
        word = struct.unpack_from('<I', raw, offset)[0]
        op, rs = word >> 26, word >> 21 & 31
        if op not in _MEMORY or rs != 28:
            continue
        displacement = (word & 0x7fff) - (word & 0x8000)
        address = references[primary]['gp'] + displacement
        groups.setdefault(address, []).append((offset, word, op, *_MEMORY[op]))

    identities = {}
    for address, uses in groups.items():
        widths = {width for _, _, _, width, _ in uses}
        loads = {op for _, _, op, _, write in uses if not write}
        families = {op in (49, 57) for _, _, op, _, _ in uses}
        if len(widths) != 1 or len(loads) > 1 or len(families) != 1:
            continue
        width = widths.pop()
        targets, evidence = [], []
        for number, ref in references.items():
            resolved, slots = set(), set()
            for offset, word, op, _, write in uses:
                native = struct.unpack_from('<I', ref['raw'], offset)[0]
                displacement = (native & 0x7fff) - (native & 0x8000)
                actual = ref['gp'] + displacement
                if ((word ^ native) & 0xffff0000
                        or not _base_unchanged(ref['raw'], -4, offset, 28)
                        or not _in_data(ref, actual, width, write)):
                    break
                resolved.add(actual)
                slots.add(displacement)
            else:
                if len(resolved) == len(slots) == 1:
                    target = resolved.pop()
                    if _possible_absolute_alias(ref['raw'], target, width):
                        break
                    targets.append(target)
                    evidence.append(dict(level=number, target=target, gp=ref['gp'],
                                         gpOffset=slots.pop(), address=ref['address'],
                                         referenceHash=hashlib.sha256(ref['raw']).hexdigest()))
                    continue
            break
        if (len(evidence) == len(references) and len(set(targets)) > 1
                and len({row['gpOffset'] for row in evidence}) > 1):
            identities[address] = (width, targets, evidence)

    replacements, proofs = {}, []
    for address, (width, targets, evidence) in identities.items():
        # Compare with every other direct GP access, including a group that
        # failed recovery. Separate externs must not create overlapping objects
        # in the primary level or in a secondary copy only.
        overlap = False
        for other, other_uses in groups.items():
            if other == address:
                continue
            for (number, ref), target in zip(references.items(), targets):
                for offset, _, _, other_width, _ in other_uses:
                    native = struct.unpack_from('<I', ref['raw'], offset)[0]
                    other_target = ref['gp'] + (native & 0x7fff) - (native & 0x8000)
                    if target < other_target + other_width and other_target < target + width:
                        overlap = True
        if overlap:
            continue
        symbol = f'D_L{primary:02d}_{address:08X}'
        for offset, word, op, _, _ in groups[address]:
            line = lines[offset // 4]
            instruction = line[0].split('*/', 1)[1].strip()
            mnemonic, operand = instruction.split(None, 1)
            matched = _OPERAND.fullmatch(operand.strip())
            rt = word >> 16 & 31
            register = 'f' + str(rt) if op in (49, 57) else _GPRS[rt]
            if (mnemonic != _OPS[op] or not matched
                    or matched[1].split(',', 1)[0] != '$' + register
                    or int(matched[2], 0) != (word & 0x7fff) - (word & 0x8000)):
                break
            replacements[line.start()] = (line, line[0][:-len(operand)]
                                           + matched[1] + f'%gp_rel({symbol})($gp)')
        else:
            proofs.append(dict(symbol=symbol, primaryLevel=primary, width=width,
                               offsets=[row[0] for row in groups[address]],
                               placements=evidence))
            continue
        # A listing mismatch rejects this entire operand identity.
        for offset, *_ in groups[address]:
            replacements.pop(lines[offset // 4].start(), None)
    for _, (line, replacement) in sorted(replacements.items(), reverse=True):
        assembly = assembly[:line.start()] + replacement + assembly[line.end():]
    return assembly, proofs
