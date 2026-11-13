"""Bounded native input evidence for primitive RAC3 m2c context declarations.

No signature, expression, call argument or source/context byte is rewritten.
Positive prefix observations are not a complete ABI or semantic contract.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import re
import struct

import reference_boundaries
import rough
from .ee_common import complete_cfg


MAX_DECLARATIONS = 16
MAX_FUNCTIONS = 48
MAX_PLACEMENTS = 64
MAX_BYTES = 32768
PREFIX_WORDS = 64
MAX_FORWARD_CALLS = 2
GPRS = tuple(range(4, 12))
FPRS = tuple(range(12, 20))


def _effects(word):
    """Small exact EE prefix subset: reads, writes and value-preserving copy.

    Registers are (bank, number). An unsupported instruction ends the prefix;
    it never grants permission to carry provenance through that instruction.
    """
    op, rs, rt, rd, fn = word >> 26, word >> 21 & 31, word >> 16 & 31, word >> 11 & 31, word & 63
    g, f = lambda n: ('gpr', n), lambda n: ('fpr', n)
    if word == 0:
        return [], [], None
    if op == 0 and fn in (33, 37, 38, 45) and word >> 6 & 31 == 0:
        if fn == 38 and rs == rt:  # XOR of identical inputs is independent of them.
            return [], [g(rd)], None
        # Only DADDU/OR/XOR with zero preserve all relevant low 64 bits.
        copy = (g(rd), g(rs if rt == 0 else rt)) if fn != 33 and (rs == 0 or rt == 0) else None
        return [g(rs), g(rt)], [g(rd)], copy
    if op in (9, 13, 25):
        # ADDIU's 32-bit sign extension is deliberately not an identity move.
        copy = (g(rt), g(rs)) if op in (13, 25) and word & 0xffff == 0 else None
        return [g(rs)], [g(rt)], copy
    if op == 15 and rs == 0:
        return [], [g(rt)], None
    if op in (32, 33, 35, 36, 37, 39, 55):
        return [g(rs)], [g(rt)], None
    if op in (40, 41, 43, 63):
        return [g(rs), g(rt)], [], None
    if op in (49, 57):
        return ([g(rs)], [f(rt)], None) if op == 49 else ([g(rs), f(rt)], [], None)
    if op in (54, 62):
        # Real LQC2/SQC2 address operands read GPRs, never write their base.
        return [g(rs)], [], None
    if op == 17:
        if rs == 0 and word & 0x7ff == 0:  # MFC1
            return [f(rd)], [g(rt)], None
        if rs == 4 and word & 0x7ff == 0:  # MTC1
            return [g(rt)], [f(rd)], None
        fd = word >> 6 & 31
        if rs == 16 and fn == 6 and rt == 0:  # MOV.S
            return [f(rd)], [f(fd)], (f(fd), f(rd))
        if rs == 16 and fn in (0, 1, 2, 3):
            return [f(rd), f(rt)], [f(fd)], None
    return None


def _control(word):
    op, rs, fn = word >> 26, word >> 21 & 31, word & 63
    return (op in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23)
            or op == 0 and fn in (8, 9)
            or op in (16, 17, 18) and rs == 8)


def prefix_inputs(raw, start, resolve=None, depth=0):
    """Observe reads before the first branch/return or after at most two calls.

    A direct JAL and its delay slot may forward unchanged incoming values to
    a hash/CFG/boundary-checked callee supplied by ``resolve``. The scan stops
    there, even if that call has no known input; caller-clobbered registers
    are never propagated past a call. No memory spill or reload is an identity.
    """
    if (not isinstance(raw, bytes) or not raw or len(raw) % 4 or len(raw) > MAX_BYTES
            or not isinstance(start, int) or start < 0 or start % 4):
        return []
    state = {(bank, number): (bank, number)
             for bank, numbers in (('gpr', GPRS), ('fpr', FPRS)) for number in numbers}
    findings = []

    def step(word, offset):
        effects = _effects(word)
        if effects is None:
            return False
        reads, writes, copy = effects
        for register in reads:
            origin = state.get(register)
            if origin is not None:
                findings.append(dict(bank=origin[0], register=origin[1], readRegister=list(register),
                                     readOffset=offset, callPath=[]))
        before = state.get(copy[1]) if copy else None
        for register in writes:
            if register != ('gpr', 0):
                state.pop(register, None)
        if copy and copy[0] != ('gpr', 0) and before is not None:
            state[copy[0]] = before
        return True

    for offset in range(0, min(len(raw), PREFIX_WORDS * 4), 4):
        word = struct.unpack_from('<I', raw, offset)[0]
        op = word >> 26
        if op == 3:
            if offset + 8 > len(raw) or _control(struct.unpack_from('<I', raw, offset + 4)[0]):
                break
            # JAL defines RA before the delay slot. No argument identity is RA.
            state.pop(('gpr', 31), None)
            if not step(struct.unpack_from('<I', raw, offset + 4)[0], offset + 4):
                break
            if resolve is not None and depth < MAX_FORWARD_CALLS:
                target = ((start + offset + 4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
                for use in resolve(target, depth + 1):
                    origin = state.get((use['bank'], use['register']))
                    if origin is not None:
                        call = dict(offset=offset, target=target, register=[use['bank'], use['register']])
                        if 'entryEvidence' in use:
                            call['callee'] = use['entryEvidence']
                        findings.append(dict(use, bank=origin[0], register=origin[1],
                                             callPath=[call, *use['callPath']]))
            break
        if _control(word) or not step(word, offset):
            break
    # Exact duplicate reads are immaterial, but keep every distinct native use.
    distinct = {repr(row): row for row in findings}
    return list(distinct.values())


def _declaration(declaration):
    matched = rough.DECL.fullmatch(declaration)
    if not matched or not rough.context_parameters(matched[2]) or '...' in matched[2]:
        return None
    params = [p.strip() for p in matched[2].split(',')]
    no_parameters = params in ([''], ['void'])
    floating = any('*' not in p and re.match(r'(?:const\s+)?(?:f32|f64|float|double)\b', p)
                   for p in params)
    return matched[1], no_parameters, not floating


def _preliminary(engine, place, depth=0):
    """Applicability only: no observation escapes without full verification."""
    def resolve(target, next_depth):
        name = engine._symbols.get(place[0], {}).get(target)
        if name is None:
            return []
        choices = [tuple(p[:4]) for p in engine.all_places(name) if p[0] == place[0] and p[2] == target]
        return _preliminary(engine, choices[0], next_depth) if len(choices) == 1 else []
    return prefix_inputs(engine.disc_bytes(place), place[2], resolve, depth)


class _Evidence:
    def __init__(self, engine):
        self.engine, self.references, self.scans = engine, {}, {}

    def verified(self, name):
        if name in self.references:
            return self.references[name]
        if len(self.references) >= MAX_FUNCTIONS or name in self.engine.remnants:
            raise ValueError('Native function bound or remnant')
        places = [tuple(p[:4]) for p in self.engine.all_places(name)]
        canonical = [tuple(p[:4]) for p in self.engine.aliases.get(name, [self.engine.places[name]])]
        if (not places or len(places) > MAX_PLACEMENTS or len(set(places)) != len(places)
                or len(set(canonical)) != len(canonical) or set(places) != set(canonical)
                or tuple(self.engine.place(name)[:4]) != places[0]
                or not re.fullmatch(r'func_(?:L\d\d_)?[0-9A-F]{8}', name)
                or places[0][2] != int(name[-8:], 16)):
            raise ValueError('Incomplete or changed canonical placements')
        boundary = reference_boundaries.inspect(self.engine, name)
        if not boundary['complete'] or not boundary['eligible'] or len(boundary['placements']) != len(places):
            raise ValueError('Incomplete or ambiguous incoming boundary evidence')
        result = {}
        for place in places:
            program, section, address, size = place
            if size <= 0 or size > MAX_BYTES or size % 4:
                raise ValueError('Invalid native window')
            identity = self.engine.reference_identity(place)
            blob = Path(identity['path']).read_bytes()
            algorithm = identity['algorithm']
            if algorithm not in ('sha1', 'sha256') or hashlib.new(algorithm, blob).hexdigest() != identity['digest'].lower():
                raise ValueError('Changed native reference')
            elf = self.engine.ELFFile(io.BytesIO(blob))
            if (elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_MIPS'
                    or elf['e_type'] != 'ET_EXEC'):
                raise ValueError('Invalid native ELF')
            sec = elf.get_section_by_name(section)
            if (sec is None or not sec['sh_flags'] & 4 or sec['sh_type'] == 'SHT_NOBITS'
                    or not sec['sh_addr'] <= address < address + size <= sec['sh_addr'] + sec['sh_size']):
                raise ValueError('Native window outside executable section')
            raw = sec.data()[address - sec['sh_addr']:address - sec['sh_addr'] + size]
            if not complete_cfg(raw, address) or self.engine.disc_bytes(place) != raw:
                raise ValueError('Incomplete CFG or inconsistent native reader')
            result[place] = dict(raw=raw, bodySha256=hashlib.sha256(raw).hexdigest(),
                                 referenceSha256=hashlib.sha256(blob).hexdigest())
        self.references[name] = result
        return result

    def scan(self, name, place, depth=0):
        key = name, place, depth
        if key in self.scans:
            return self.scans[key]
        reference = self.verified(name)[place]
        def resolve(target, next_depth):
            callee = self.engine._symbols.get(place[0], {}).get(target)
            if callee is None:
                return []
            candidates = [p for p in self.verified(callee) if p[0] == place[0] and p[2] == target]
            if len(candidates) != 1:
                return []
            return self.scan(callee, candidates[0], next_depth)
        result = prefix_inputs(reference['raw'], place[2], resolve, depth)
        entry = dict(name=name, program=place[0], section=place[1], address=place[2], size=place[3],
                     bodySha256=reference['bodySha256'], referenceSha256=reference['referenceSha256'])
        result = [dict(use, entryEvidence=entry) for use in result]
        self.scans[key] = result
        return result


def inspect(engine, declarations):
    """Diagnose zero-arity GPR and no-floating-formal FP native observations.

    This deliberately does not map arbitrary C types to ABI slots. A declaration
    containing floating formals suppresses FP diagnostics; nonzero arity
    suppresses GPR diagnostics. Absence of findings never establishes an ABI.
    """
    result = dict(findings=[], unavailable=[], primaryOnlySkipped=[], mutatesInputs=False, semanticApproval=False,
                  scope='Bounded entry prefixes, direct input reads and at most two direct-call forwarding levels; not a complete ABI.')
    evidence = _Evidence(engine)
    if len(declarations) > MAX_DECLARATIONS:
        result['unavailable'].append(dict(reason='Context declaration bound exceeded'))
        return result
    for declaration in declarations:
        parsed = _declaration(declaration)
        if parsed is None:
            continue
        name, no_parameters, no_float = parsed
        try:
            def relevant(use):
                return use['bank'] == 'gpr' and no_parameters or use['bank'] == 'fpr' and no_float
            if not any(map(relevant, _preliminary(engine, tuple(engine.place(name)[:4])))):
                result['primaryOnlySkipped'].append(name)
                continue
            rows = []
            for place, reference in evidence.verified(name).items():
                uses = list(filter(relevant, evidence.scan(name, place)))
                rows.append(dict(program=place[0], section=place[1], address=place[2], size=place[3],
                    bodySha256=reference['bodySha256'], referenceSha256=reference['referenceSha256'], uses=uses))
            # All copies must support the same slots and use/forwarding offsets.
            def identity(use):
                return (use['bank'], use['register'], tuple(use['readRegister']), use['readOffset'],
                        tuple((p['offset'], tuple(p['register'])) for p in use['callPath']))
            common = set.intersection(*(set(map(identity, row['uses'])) for row in rows))
            slots = sorted({(use[0], use[1]) for use in common})
            if slots:
                for row in rows:
                    row['uses'] = [use for use in row['uses'] if identity(use) in common]
                result['findings'].append(dict(name=name, declaration=declaration,
                    observedIncomingRegisters=[dict(bank=bank, register=number) for bank, number in slots],
                    placements=rows, requiresManualContextReview=True))
        except Exception as error:
            result['unavailable'].append(dict(name=name, reason=str(error)[:300]))
    return result


def warnings(evidence, language=None):
    if not evidence['findings']:
        return []
    german = (language or os.environ.get('PS2DECOMP_LANGUAGE', 'en')).lower().split('-')[0] == 'de'
    names = ', '.join(row['name'] for row in evidence['findings'])
    return [('RAC3-Kontext: Originalinstruktionen lesen ankommende Register, die die Parameterdeklaration nicht ausdrückt: '
             + names + '. contextInputs prüfen; keine Signatur oder Argumente automatisch geändert.') if german else
            ('RAC3 context: native instructions read incoming registers not expressed by the parameter declaration: '
             + names + '. Review contextInputs; signatures and call arguments have not been changed.')]
