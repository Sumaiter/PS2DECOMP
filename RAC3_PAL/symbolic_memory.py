"""Offer native-evidenced names for typed constant-pointer accesses.

This adapter rule preserves the existing access type, value expressions and
statement order. It neither reads an initial value into C nor certifies a
match. In particular a literal address with no object relocation is not proof
of a shared level global: every placement must corroborate the native access.
"""
from __future__ import annotations

import copy
import ast
import re
import struct

from .ee_common import complete_cfg
from search_rules import code_only

LABEL = 'rac3-native-symbolic-memory'

# Deliberately omit long/long long: SN's widths cannot be inferred from the
# host, an alias name such as s64, or ELFCLASS32 alone.
_TYPES = {
    'char': (1, (32, 36), (40,)),
    'signed char': (1, (32,), (40,)),
    'unsigned char': (1, (36,), (40,)),
    'short': (2, (33,), (41,)),
    'signed short': (2, (33,), (41,)),
    'unsigned short': (2, (37,), (41,)),
    'int': (4, (35,), (43,)),
    'signed int': (4, (35,), (43,)),
    'signed': (4, (35,), (43,)),
    'unsigned': (4, (35, 39), (43,)),
    'unsigned int': (4, (35, 39), (43,)),
    'float': (4, (49,), (57,)),
}
_LOADS = {32, 33, 35, 36, 37, 39, 49}
_STORES = {40, 41, 43, 57}


def _header_macros(engine, variants, source):
    """A typedef header plus RAC3's unreferenced assembly metadata macros.

    The parser prelude is not a preprocessor. Reject conditional typedefs and
    type macros; the real project's one include_asm.h may only add unreferenced
    macros and assembly include metadata, never C types or declarations.
    """
    if variants.header_text != (engine.root / 'include/common.h').read_text(encoding='utf-8'):
        return None
    if variants.plain_header():
        return set()
    text = variants.header_text
    include = r'(?m)^\s*#\s*include\s*"include_asm\.h"\s*$'
    if len(re.findall(include, code_only(text))) != 1:
        return None
    probe = copy.copy(variants)
    probe.header_text = re.sub(include, '', text)
    if not probe.plain_header():
        return None
    metadata = (engine.root / 'include/include_asm.h').read_text(encoding='utf-8')
    metadata = code_only(metadata).replace('\r\n', '\n').replace('\\\n', '')
    macros = set()
    for directive in re.findall(r'(?m)^\s*#([^\n]*)$', metadata):
        head = directive.strip().split(None, 1)[0]
        if head not in ('define', 'if', 'ifdef', 'ifndef', 'elif', 'else', 'endif'):
            return None
        if head == 'define':
            match = re.match(r'\s*define\s+([A-Za-z_]\w*)', directive)
            if not match:
                return None
            macros.add(match[1])
    if macros.intersection(re.findall(r'[A-Za-z_]\w*', code_only(source))):
        return None
    body = re.sub(r'(?m)^\s*#[^\n]*$', '', metadata)
    includes = []
    def remove_include(match):
        includes.append(ast.literal_eval('"' + match[1] + '"'))
        return ''
    body = re.sub(r'__asm__\(\s*"((?:\\.|[^"\\])*)"\s*\)\s*;', remove_include, body)
    if body.strip() or any(not re.fullmatch(r'\.include "include/[A-Za-z_]\w*\.inc"\n', value)
                           for value in includes):
        return None
    return macros


def _native_accesses(raw, start, gp):
    """Straight-line proven addresses; unknown/reassigned bases never survive.

    Calls retain only EABI preserved registers and GP, after the delay slot.
    A branch merge, opaque opcode or GP write rejects this narrow rule rather
    than letting a stale LUI constant become address evidence.
    """
    if (not isinstance(raw, bytes) or not raw or len(raw) > 32768
            or len(raw) % 4 or not complete_cfg(raw, start)):
        return None
    regs = {0: (0, False), 28: (gp, True)}
    accesses = {}
    clobber_at = None
    words = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
    for index, word in enumerate(words):
        op, rs, rt, rd = word >> 26, word >> 21 & 31, word >> 16 & 31, word >> 11 & 31
        imm = (word & 0x7fff) - (word & 0x8000)
        if op in _LOADS | _STORES and rs in regs:
            base, from_gp = regs[rs]
            address = (base + imm) & 0xffffffff
            accesses[index * 4] = (address, op, word & 0xffff0000, from_gp)
        written = None
        value = None
        if word == 0:
            pass
        elif op == 15 and rs == 0:
            written, value = rt, ((word & 0xffff) << 16, False)
        elif op in (9, 25):
            written = rt
            if rs in regs:
                value = ((regs[rs][0] + imm) & 0xffffffff, regs[rs][1])
        elif op == 13:
            written = rt
            if rs in regs and not regs[rs][1]:
                value = (regs[rs][0] | (word & 0xffff), False)
        elif op in (8, 10, 11, 12, 14):
            written = rt
        elif op in _LOADS | {26, 27, 30, 34, 38, 55}:
            # COP1 loads write an FP register, not the same-numbered GPR.
            written = rt if op != 49 else None
        elif op in _STORES | {31, 42, 44, 45, 46, 53, 61, 63}:
            pass
        elif op == 3:
            # JAL writes the link register before executing its delay slot.
            # Never let a prior LUI-to-RA become evidence for a delay read.
            written = 31
            clobber_at = index + 1
        elif op in (17, 18):
            if rs in (0, 2):
                written = rt
            elif rs not in (4, 6, 16, 17, 20, 21, 22):
                return None
        elif op == 0:
            fn = word & 63
            if fn == 8 and rs == 31:
                pass
            elif fn in (0, 2, 3, 4, 6, 7, 10, 11, 16, 18,
                        20, 22, 23, 32, 33, 34, 35, 36, 37, 38, 39,
                        42, 43, 44, 45, 46, 47, 56, 58, 59, 60, 62, 63):
                written = rd
                if (fn in (33, 37, 45) and (rs == 0 or rt == 0)
                        and (word >> 6 & 31) == 0):
                    value = regs.get(rt if rs == 0 else rs)
            elif fn in (24, 25):
                # R5900 MULT/MULTU may also write a nonzero rd (three-operand
                # form); that register cannot retain a previous address.
                written = rd
            elif fn not in (17, 19, 26, 27):
                return None
        else:
            return None
        if written == 28:
            return None
        if written not in (None, 0):
            if value is None:
                regs.pop(written, None)
            else:
                regs[written] = value
        if index == clobber_at:
            regs = {reg: val for reg, val in regs.items()
                    if reg in (0, 28, 29) or 16 <= reg <= 23}
            clobber_at = None
    return accesses


def _data_range(engine, program, address, width, write):
    elf = engine.elf(program)
    if elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_MIPS':
        return False
    for sec in elf.iter_sections():
        flags = sec['sh_flags']
        if (flags & 2 and not flags & 4
                and sec['sh_type'] in ('SHT_PROGBITS', 'SHT_NOBITS')
                and (not write or flags & 1)
                and sec['sh_addr'] <= address
                and address + width <= sec['sh_addr'] + sec['sh_size']):
            return True
    return False


def children(engine, name, source, variants):
    """Return one bounded, unapproved candidate, or none when evidence is vague."""
    try:
        return _children(engine, name, source, variants)
    except Exception:
        # Parsers and reference providers use different exception classes.
        # An optional search rule must fail closed, not weaken those checks.
        return []


def _children(engine, name, source, variants):
    macros = _header_macros(engine, variants, source)
    if macros is None:
        return []
    a = variants.a
    tree, fn = copy.deepcopy(variants.parse(source))
    if fn.decl.name != name:
        return []
    if any(isinstance(n, a.Typedef) for *_, n in variants._nodes(fn.body)):
        return []
    nodes = list(variants._nodes(tree))
    # Emitting an AST must not change source-context builtins or execute hidden
    # macros/assembly. Variants.parse permits only the common.h include.
    if any('asm' in type(n).__name__.lower()
           or 'register' in getattr(n, 'storage', ())
           or (isinstance(n, a.ID) and (n.name.startswith('__') or n.name in ('asm', '_asm')))
           for *_, n in nodes):
        return []
    prelude = variants.parser().parse(variants.prelude)
    aliases = {n.name: n.type for n in prelude.ext if isinstance(n, a.Typedef)}
    for n in tree.ext:
        if isinstance(n, a.Typedef):
            if n.name in aliases or any(getattr(n, key, None) for key in
                                       ('quals', 'align', 'gcc_attributes', 'asmlabel')):
                return []
            aliases[n.name] = n.type
        elif (not isinstance(n, a.FuncDef)
              and not (isinstance(n, a.Decl) and n.init is None
                       and isinstance(n.type, a.FuncDecl))):
            # A pre-existing data declaration might alias a new symbolic name.
            # Its complete ownership/range is outside this narrow recovery.
            return []

    def primitive(ty, seen=frozenset()):
        if (not isinstance(ty, a.TypeDecl) or ty.quals
                or any(getattr(ty, k, None) for k in ('align', 'gcc_attributes', 'asmlabel'))
                or not isinstance(ty.type, a.IdentifierType)):
            return None
        names = ty.type.names
        if len(names) == 1 and names[0] in aliases:
            return None if names[0] in seen else primitive(aliases[names[0]], seen | {names[0]})
        text = ' '.join(names)
        return text if text in _TYPES else None

    records = []
    for parent, attr, index, node in variants._nodes(fn.body):
        if not (isinstance(node, a.UnaryOp) and node.op == '*'
                and isinstance(node.expr, a.Cast)):
            continue
        cast = node.expr
        pointer = cast.to_type.type
        if (cast.to_type.quals or not isinstance(pointer, a.PtrDecl) or pointer.quals
                or any(getattr(pointer, k, None) for k in ('align', 'gcc_attributes', 'asmlabel'))):
            continue
        ty = primitive(pointer.type)
        value = cast.expr
        if not isinstance(value, a.Constant) or value.type not in ('int', 'unsigned int'):
            continue
        if ty is None:
            return []
        try:
            address = int(re.sub(r'[uUlL]+$', '', value.value), 0)
        except ValueError:
            return []
        width = _TYPES[ty][0]
        if not 0 < address <= 0xffffffff - width + 1 or address % width:
            return []
        write = isinstance(parent, a.Assignment) and parent.lvalue is node
        if ((write and parent.op != '=') or
                isinstance(parent, a.UnaryOp) and parent.op in ('&', 'p++', 'p--', '++', '--')):
            continue
        spelling = ' '.join(pointer.type.type.names)
        records.append((parent, attr, index, node, address, width, ty, spelling, write))
    if not records or len(records) > 32:
        return []
    rewritten_casts = {id(record[3].expr) for record in records}
    for *_, node in variants._nodes(fn.body):
        if (isinstance(node, a.Cast) and isinstance(node.to_type.type, a.PtrDecl)
                and isinstance(node.expr, a.Constant)
                and node.expr.type in ('int', 'unsigned int')
                and id(node) not in rewritten_casts):
            # A remaining literal pointer could reach the same object through
            # a second identity (a local base, escaped pointer or other type).
            return []
    # An address under several unrelated types, or any already defined name,
    # needs an ownership/alias review instead of a new extern declaration.
    signatures = {}
    for *_, address, width, ty, spelling, write in records:
        signatures.setdefault(address, set()).add((width, ty, spelling))
    if any(len(types) != 1 for types in signatures.values()):
        return []
    places = [tuple(p[:4]) for p in engine.all_places(name)]
    primary = tuple(engine.place(name)[:4])
    if not places or len(places) != len(set(places)) or primary not in places:
        return []
    maps, gps = [], []
    for place in [primary] + [p for p in places if p != primary]:
        engine.assert_reference(place[0])
        gp = engine.validated_gp(place[0])
        raw = engine.disc_bytes(place)
        if len(raw) != place[3]:
            return []
        accesses = _native_accesses(raw, place[2], gp)
        if accesses is None:
            return []
        maps.append((place, accesses))
        gps.append(gp)
    primary_accesses = maps[0][1]
    names = {getattr(n, 'name', None) for *_, n in nodes} | macros
    declarations, replacements, ownership, ranges = {}, {}, {}, {}
    for _, _, _, _, address, width, ty, spelling, write in records:
        matching = {off: access for off, access in primary_accesses.items()
                    if access[0] == address and access[1] in _TYPES[ty][2 if write else 1]}
        if not matching:
            return []
        basis = {entry[3] for entry in matching.values()}
        if len(basis) != 1:
            return []
        from_gp = basis.pop()
        targets = []
        for place, accesses in maps:
            resolved = set()
            for off, original in matching.items():
                actual = accesses.get(off)
                if (actual is None or actual[1:] != original[1:]
                        or not _data_range(engine, place[0], actual[0], width, write)):
                    return []
                resolved.add(actual[0])
            if len(resolved) != 1:
                return []
            targets.append(resolved.pop())
        identity = (from_gp, tuple(targets))
        if address in ownership and ownership[address] != identity:
            # A primary read and write may coincide while their other-level
            # targets differ. They must never become one shared declaration.
            return []
        ownership[address] = identity
        for old_address, (old_width, old_targets) in ranges.items():
            if old_address != address and any(
                    left < right + old_width and right < left + width
                    for left, right in zip(targets, old_targets)):
                # Separate extern declarations would tell GCC that overlapping
                # bytes belong to independent objects, changing alias semantics.
                return []
        ranges[address] = (width, targets)
        if from_gp:
            slot = address - gps[0]
            if any(target - gp != slot for target, gp in zip(targets, gps)):
                return []
            story_gp = engine.validated_gp('frontbin')
            canonical = story_gp + slot
            if not 0 <= canonical <= 0xffffffff:
                return []
            symbol = f'D_{canonical:08X}_sda'
            declaration = f'extern {spelling} {symbol};'
            replacement = a.ID(symbol)
        else:
            level = re.fullmatch(r'func_L(\d\d)_[0-9A-F]{8}', name)
            if level and primary[0] == 'level_' + level[1]:
                symbol = f'D_L{level[1]}_{address:08X}'
            elif all(target == address for target in targets):
                symbol = f'D_{address:08X}'
            else:
                return []
            declaration = f'extern {spelling} {symbol}[];'
            replacement = a.ArrayRef(a.ID(symbol), a.Constant('int', '0'))
        if symbol in names:
            return []
        if address in replacements and variants.generator.visit(replacements[address]) != variants.generator.visit(replacement):
            return []
        declarations.setdefault(symbol, declaration)
        replacements[address] = replacement
    if len(declarations) > 16:
        return []
    for parent, attr, index, node, address, *_ in records:
        replacement = copy.deepcopy(replacements[address])
        if index is None:
            setattr(parent, attr, replacement)
        else:
            getattr(parent, attr)[index] = replacement
    insertion = tree.ext.index(fn)
    type_context = variants.generator.visit(a.FileAST([
        *prelude.ext, *[n for n in tree.ext if isinstance(n, a.Typedef)]]))
    for declaration in declarations.values():
        tree.ext.insert(insertion, variants.parser().parse(type_context + '\n' + declaration).ext[-1])
        insertion += 1
    return [(LABEL, variants._emit(source, tree))]
