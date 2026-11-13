"""Recover the PAL project's existing vector-library zero helper, not new asm.

SN's ``long long`` is a 16-byte TI value in this adapter, unlike its 8-byte
``long``. Real compiler probes distinguish POR+SQ for a C TI zero, SD for a
64-bit zero, and the project's qzero memory-output helper's SQ $zero. Only
the existing, exact TI helper is reused. All source sites and all native
placements must independently agree on width, offset, and pointer origin.

The second seed repairs one bounded m2c load-order loss. Its entire native
function must prove LQ / word clear / SQ-zero / return / SQ-copy. Capturing
the source before either clear preserves possible source/output aliases.
Neither seed approves types, ownership, initialization or a full game build.
"""
from __future__ import annotations

import copy
import re
import struct

from ee_common import complete_cfg
from native_guard_variants import _integer, _read
from native_load_before_clear import _writes
from search_rules import code_only


def _tokens(text):
    return re.findall(r'"(?:\\.|[^"\\])*"|[A-Za-z_]\w*|\d[\w.]*|\S', text)


def _helper_available(repository):
    text = code_only((repository / 'include/vu0.h').read_text(encoding='utf-8'))
    if '\\\n' in text or '\\\r\n' in text:
        return False
    declarations = re.findall(r'\btypedef\b[^;]*\bvu0_q\b[^;]*;', text)
    expected = 'typedef int vu0_q __attribute__((mode(TI)));'
    definitions = re.findall(r'^\s*#\s*define\s+qzero\b[^\n]*', text, re.M)
    macro = '#define qzero(d) __asm__("sq $0, %0" : "=m"(*(vu0_q *)(d)))'
    # Additional conditional branches or an undef could select a different
    # declaration despite a matching string in an inactive branch.
    conditionals = [(directive, tail.strip()) for directive, tail in re.findall(
        r'^\s*#\s*(if\w*|else|elif|endif|undef)\b([^\n]*)', text, re.M)]
    directives = [line.strip() for line in re.findall(r'^\s*#([^\n]*)$', text, re.M)]
    # The real project header has only this guard and qzero macro. Even a
    # seemingly unrelated ``#define TI DI`` changes the helper's actual width.
    if (len(directives) != 4 or directives[:2] != ['ifndef VU0_H', 'define VU0_H']
            or _tokens(directives[2]) != _tokens(macro[1:]) or directives[3] != 'endif'):
        return False
    return (len(declarations) == len(definitions) == 1
            and _tokens(declarations[0]) == _tokens(expected)
            and _tokens(definitions[0]) == _tokens(macro)
            and conditionals == [('ifndef', 'VU0_H'), ('endif', '')])


def _header_types_available(repository, variants, source):
    """Do not mistake an extracted typedef prelude for a preprocessed header."""
    raw = (repository / 'include/common.h').read_text(encoding='utf-8')
    if raw != variants.header_text or '\\\n' in raw or '\\\r\n' in raw:
        return False
    text = code_only(raw)
    directives = [line.strip() for line in re.findall(r'^\s*#([^\n]*)$', text, re.M)]
    if directives and directives[0] == 'pragma once':
        directives = directives[1:]
    guards = set()
    if directives and re.fullmatch(r'ifndef\s+[A-Za-z_]\w*', directives[0]):
        guard = directives[0].split()[1]
        if len(directives) < 3 or directives[1] != 'define '+guard or directives[-1] != 'endif':
            return False
        guards.add(guard)
        directives = directives[2:-1]
    included = False
    for directive in directives:
        if _tokens(directive) == _tokens('define NULL ((void *)0)'):
            guards.add('NULL')
        elif directive == 'include "include_asm.h"' and not included:
            included = True
        else:
            return False
    body = re.sub(r'^\s*#.*$', '', text, flags=re.M)
    if _tokens(body) != _tokens(code_only(variants.prelude)):
        return False
    if included:
        assembly = code_only((repository / 'include/include_asm.h').read_text(encoding='utf-8'))
        allowed = {'INCLUDE_ASM_H', 'ASM_FUNC', 'LINKER_REMNANT', 'TEXT_PADDING',
                   'TEXT_FILL', 'INCLUDE_ASM', 'INCLUDE_RODATA'}
        definitions = re.findall(r'^\s*#\s*define\s+([A-Za-z_]\w*)', assembly, re.M)
        if (not definitions or set(definitions) - allowed
                or re.search(r'^\s*#\s*(?:include|undef|line)\b|\btypedef\b', assembly, re.M)):
            return False
        guards |= set(definitions)
    # A macro may also replace an identifier that the AST treats as a type or
    # variable. The adapter never silently drops that preprocessing context.
    return not (set(re.findall(r'\b[A-Za-z_]\w*\b', code_only(source))) & guards)


def _sn_level_profile(engine, name, source_aliases):
    """The same PAL adapter can use ee29: region is not an ABI proof."""
    owners = list(engine.owner_paths.get(name, ()))
    if len(owners) != 1:
        return False
    owner = str(owners[0]).replace('\\', '/')
    if not owner.startswith('src/levels/') or '..' in owner.split('/'):
        return False
    context = code_only((engine.root / owner).read_text(encoding='utf-8'))
    directives = re.findall(r'^\s*#([^\n]*)$', context, re.M)
    if any(line.strip() not in ('include "common.h"', 'include "vu0.h"') for line in directives):
        return False
    # The real checker carries earlier owner declarations and may discard a
    # repeated typedef from the snippet. Unknown collisions are not the same
    # type merely because the standalone source parser accepted the alias.
    if any(re.search(r'\b' + re.escape(alias) + r'\b', context) for alias in source_aliases):
        return False
    rows = []
    for line in (engine.root / 'config/levels/text_parts.txt').read_text(encoding='utf-8').splitlines():
        fields = line.split('#', 1)[0].split()
        if fields and fields[0] == owner:
            rows.append(fields[1:])
    allowed = {'-O2', '-G8', '-fopt-stack', '-mno-check-zero-division',
               '-mno-split-addresses', '-mvu0-use-vf0-vf2', '@ps2as', '@newas'}
    return (len(rows) == 1 and {'-O2', '-G8'} <= set(rows[0])
            and not set(rows[0]) - allowed)


def _canonical(node, a, aliases, seen=frozenset()):
    if (not isinstance(node, a.TypeDecl) or not isinstance(node.type, a.IdentifierType)
            or any(getattr(node, field, None) for field in ('quals', 'gcc_attributes', 'asmlabel', 'align'))):
        return None
    names = node.type.names
    if len(names) == 1 and names[0] in aliases:
        alias = names[0]
        return _canonical(aliases[alias], a, aliases, seen | {alias}) if alias not in seen else None
    spelling = ' '.join(names)
    return spelling if spelling in {
        'void', 'char', 'signed char', 'unsigned char', 'short', 'signed short', 'unsigned short',
        'int', 'signed int', 'unsigned int', 'long', 'unsigned long',
        'long long', 'signed long long', 'unsigned long long', 'float', 'double',
    } else None


def _pointer(node, a, aliases):
    return (isinstance(node, a.PtrDecl) and not node.quals
            and (_canonical(node.type, a, aliases) is not None
                 or _pointer(node.type, a, aliases)))


def _wide(node, a, aliases):
    return _canonical(node, a, aliases) in ('long long', 'signed long long', 'unsigned long long')


def _address(base, offset):
    while base and base[0] == 'add':
        base, extra = base[1:]
        offset += extra
    return base, offset


def _loaded(base, offset, epoch):
    base, offset = _address(base, offset)
    return 'load', base, offset, epoch


def _fields(word):
    return word >> 26, word >> 21 & 31, word >> 16 & 31, (word & 0x7fff) - (word & 0x8000)


def _native_sites(raw, start, parameter_count):
    """Straight-line provenance; every SQ-zero needs a known actual base."""
    if (not isinstance(raw, (bytes, bytearray)) or len(raw) < 8 or len(raw) % 4
            or len(raw) > 32768 or not isinstance(start, int) or isinstance(start, bool)
            or start % 4 or not 0 <= start <= 0xffffffff - len(raw)
            or not complete_cfg(raw, start)):
        return None
    words = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
    registers = {4+i: ('arg', i) for i in range(parameter_count)}
    registers[29] = ('stack', 0)
    sites, epoch, memory_writes, pending_call = [], 0, 0, False
    for index, word in enumerate(words):
        op, rs, rt, displacement = _fields(word)
        delay = index > 0 and (words[index-1] >> 26 == 3 or words[index-1] == 0x03e00008)
        if op == 3:
            if delay or index + 2 >= len(words):
                return None
            pending_call = True
            registers.pop(31, None)  # JAL writes RA before executing its slot.
            continue
        if word == 0x03e00008:
            if delay or index + 2 != len(words):
                return None
            continue
        if (op in (1, 2, 4, 5, 6, 7, 20, 21, 22, 23)
                or op == 17 and rs == 8 or op == 0 and word & 63 in (8, 9)):
            return None
        # R5900 MULT/MULTU optionally write rd as well as HI/LO. The older
        # shared write-set helper models only ordinary MIPS' HI/LO form.
        if op == 0 and word & 63 in (24, 25) and word >> 11 & 31:
            return None
        old = dict(registers)
        if op == 31 and rt == 0:
            if delay or rs not in old or displacement % 16:
                return None
            sites.append(_address(old[rs], displacement))
        for register in _writes(word):
            registers.pop(register, None)
        if op == 35 and rs in old and rt not in (0, 29, 31):
            registers[rt] = _loaded(old[rs], displacement, (epoch, memory_writes))
        elif op == 9 and rs in old and rt not in (0, 31):
            registers[rt] = ('add', old[rs], displacement)
        elif op == 0 and word & 63 in (33, 37, 45) and word >> 6 & 31 == 0 and (rs == 0 or rt == 0):
            original, destination = rt if rs == 0 else rs, word >> 11 & 31
            if original in old and destination not in (0, 31):
                registers[destination] = old[original]
        if op in (31, 40, 41, 42, 43, 44, 45, 46, 57, 61, 62, 63):
            base = _address(old.get(rs), displacement)[0]
            if not base or base[0] != 'stack':
                memory_writes += 1
        if delay and pending_call:
            for register in (*range(1, 16), 24, 25, 31):
                registers.pop(register, None)
            epoch += 1
            pending_call = False
    return sites


def _source_sites(fn, rules, aliases):
    a = rules.a
    params = fn.decl.type.args.params if fn.decl.type.args else []
    if (not 1 <= len(params) <= 8 or any(not isinstance(p, a.Decl) or not p.name
                                       or not _pointer(p.type, a, aliases) for p in params)):
        return None
    declarations = [n for *_, n in rules._nodes(fn) if isinstance(n, a.Decl) and n.name]
    if len({d.name for d in declarations}) != len(declarations):
        return None
    pointers = {d.name for d in declarations if _pointer(d.type, a, aliases)}
    writes = {}
    for *_, node in rules._nodes(fn.body):
        if isinstance(node, a.Typedef):
            return None
        if isinstance(node, (a.If, a.For, a.While, a.DoWhile, a.Switch, a.Goto, a.Label, a.TernaryOp)):
            return None
        if isinstance(node, a.Compound) and node is not fn.body:
            return None
        if isinstance(node, a.UnaryOp) and node.op in ('&', '++', '--', 'p++', 'p--'):
            if any(isinstance(n, a.ID) and n.name in pointers for *_, n in rules._nodes(node)):
                return None
        if isinstance(node, a.Assignment) and isinstance(node.lvalue, a.ID) and node.lvalue.name in pointers:
            writes[node.lvalue.name] = writes.get(node.lvalue.name, 0) + 1
        if isinstance(node, a.Decl) and node.name in pointers and node.init is not None:
            writes[node.name] = writes.get(node.name, 0) + 1
    if any(writes.get(p.name) for p in params) or any(count > 1 for count in writes.values()):
        return None
    bindings = {p.name: ('arg', i) for i, p in enumerate(params)}
    sites, assignments, epoch, memory_writes = [], [], 0, 0
    for item in fn.body.block_items or []:
        if isinstance(item, a.FuncCall):
            epoch += 1
            continue
        if any(isinstance(n, a.FuncCall) for *_, n in rules._nodes(item)):
            return None
        target, value = None, None
        if isinstance(item, a.Decl) and item.init is not None and item.name in pointers:
            target, value = item.name, item.init
        elif isinstance(item, a.Assignment) and item.op == '=':
            if isinstance(item.lvalue, a.ID) and item.lvalue.name in pointers:
                target, value = item.lvalue.name, item.rvalue
            if (isinstance(item.lvalue, a.UnaryOp) and item.lvalue.op == '*'
                    and isinstance(item.lvalue.expr, a.Cast)
                    and isinstance(item.lvalue.expr.to_type.type, a.PtrDecl)
                    and _wide(item.lvalue.expr.to_type.type.type, a, aliases)
                    and _integer(item.rvalue, a) == 0):
                read = _read(item.lvalue, a, aliases)
                if (not read or not isinstance(read[1], a.ID) or read[1].name not in bindings
                        or read[2] % 16):
                    return None
                sites.append(_address(bindings[read[1].name], read[2]))
                assignments.append(item)
        if target:
            read = _read(value, a, aliases)
            if (not read or not _pointer(read[0], a, aliases)
                    or not isinstance(read[1], a.ID) or read[1].name not in bindings
                    or read[2] % 4):
                return None
            bindings[target] = _loaded(bindings[read[1].name], read[2], (epoch, memory_writes))
        elif isinstance(item, a.Assignment):
            if not isinstance(item.lvalue, a.ID) or item.lvalue.name not in {d.name for d in declarations}:
                memory_writes += 1
    return params, sites, assignments


def _recover_copy(tree, fn, rules, aliases, words, params):
    """Recognize a complete native dataflow, never infer disjoint pointers."""
    a = rules.a
    items = fn.body.block_items or []
    if (len(params) != 2 or len(items) != 3 or len(words) != 5
            or any(not isinstance(s, a.Assignment) or s.op != '=' for s in items)):
        return None
    clear, zero, store = items
    cr, zr, dst = (_read(s.lvalue, a, aliases) for s in items)
    value = store.rvalue
    if isinstance(value, a.Cast) and _wide(value.to_type.type, a, aliases):
        value = value.expr
    src = _read(value, a, aliases)
    if (not all((cr, zr, dst, src))
            or _canonical(cr[0], a, aliases) not in ('int', 'signed int', 'unsigned int')
            or not all(_wide(r[0], a, aliases) for r in (zr, dst, src))
            or len({_canonical(r[0], a, aliases) for r in (zr, dst, src)}) != 1
            or _integer(clear.rvalue, a) != 0 or _integer(zero.rvalue, a) != 0
            or any(not isinstance(r[1], a.ID) for r in (cr, zr, dst, src))):
        return None
    param_register = {p.name: 4+i for i, p in enumerate(params)}
    if (cr[1].name != zr[1].name or cr[1].name != dst[1].name
            or src[1].name not in param_register or dst[1].name not in param_register
            or any(r[2] % 16 for r in (zr, dst, src)) or cr[2] % 4
            or any(not (cr[2]+4 <= r[2] or r[2]+16 <= cr[2]) for r in (zr, dst))
            or not (zr[2]+16 <= dst[2] or dst[2]+16 <= zr[2])):
        return None
    lo, lb, lr, li = _fields(words[0])
    co, cb, cz, ci = _fields(words[1])
    zo, zb, zz, zi = _fields(words[2])
    so, sb, sr, si = _fields(words[4])
    if (lo != 30 or lb != param_register[src[1].name] or li != src[2]
            or lr in (0, 29, 31, param_register[dst[1].name])
            or co != 43 or cb != param_register[dst[1].name] or cz != 0 or ci != cr[2]
            or zo != 31 or zb != cb or zz != 0 or zi != zr[2]
            or words[3] != 0x03e00008 or so != 31 or sb != cb or sr != lr or si != dst[2]):
        return None
    other = copy.deepcopy(tree)
    new_fn = next(n for n in other.ext if isinstance(n, a.FuncDef))
    used = {n.name for *_, n in rules._nodes(tree) if isinstance(n, (a.ID, a.Decl, a.Typedef))}
    serial = 0
    while f'rac3_quadword_value_{serial}' in used:
        serial += 1
    name = f'rac3_quadword_value_{serial}'
    declaration = copy.deepcopy(params[0])
    declaration.name, declaration.type, declaration.init = name, copy.deepcopy(src[0]), None
    declaration.type.declname = name
    declaration.quals, declaration.storage, declaration.funcspec = [], [], []
    body = new_fn.body.block_items
    body[1] = a.FuncCall(a.ID('qzero'), a.ExprList([copy.deepcopy(zero.lvalue.expr.expr)]))
    body[2].rvalue = a.ID(name)
    body[0:0] = [declaration, a.Assignment('=', a.ID(name), copy.deepcopy(store.rvalue))]
    return other


def _emit(source, tree, rules):
    text = rules._emit(source, tree)
    return text.replace('#include "common.h"', '#include "common.h"\n#include "vu0.h"', 1)


def children(engine, name, source, variants):
    """At most two bounded seeds; ordinary history, budgets and gates still apply."""
    try:
        if (getattr(engine, 'version', None) != 'pal' or not _helper_available(engine.root)
                or not _header_types_available(engine.root, variants, source)):
            return []
        if re.search(r'\b(?:volatile|restrict|asm|__asm__|qzero|vu0_q)\b', code_only(source)):
            return []
        tree, fn = variants.parse(source)
        a = variants.a
        if fn.decl.name != name:
            return []
        nodes = [n for *_, n in variants._nodes(tree)]
        if any(any(getattr(n, field, None) for field in ('quals', 'gcc_attributes', 'asmlabel', 'align')) for n in nodes):
            return []
        if any(isinstance(n, a.ID) and n.name.startswith('__') for n in nodes):
            return []
        for declaration in tree.ext:
            if isinstance(declaration, (a.FuncDef, a.Typedef)):
                continue
            if (not isinstance(declaration, a.Decl) or declaration.init is not None
                    or not (isinstance(declaration.type, a.FuncDecl) or declaration.storage == ['extern'])):
                return []
        typedefs = [n for n in variants.parser().parse(variants.prelude).ext if isinstance(n, a.Typedef)]
        typedefs += [n for n in tree.ext if isinstance(n, a.Typedef)]
        if len({t.name for t in typedefs}) != len(typedefs):
            return []
        if any(any(getattr(n, field, None) for field in ('quals', 'gcc_attributes', 'asmlabel', 'align'))
               for definition in typedefs for *_, n in variants._nodes(definition)):
            return []
        if not _sn_level_profile(engine, name, {n.name for n in tree.ext if isinstance(n, a.Typedef)}):
            return []
        aliases = {t.name: t.type for t in typedefs}
        candidate = _source_sites(fn, variants, aliases)
        if not candidate:
            return []
        params, sites, assignments = candidate
        if not sites or len(set(sites)) != len(sites):
            return []
        placements = engine.all_places(name)
        if not placements:
            return []
        native = []
        for place in placements:
            engine.assert_reference(place[0])
            raw = engine.disc_bytes(tuple(place[:4]))
            if len(raw) != place[3] or _native_sites(raw, place[2], len(params)) != sites:
                return []
            native.append(struct.unpack('<'+'I'*(len(raw)//4), raw))
        other = copy.deepcopy(tree)
        new_fn = next(n for n in other.ext if isinstance(n, a.FuncDef))
        originals = fn.body.block_items or []
        for assignment in assignments:
            index = originals.index(assignment)
            new_fn.body.block_items[index] = a.FuncCall(a.ID('qzero'), a.ExprList([copy.deepcopy(assignment.lvalue.expr.expr)]))
        result = [('rac3-quadword-zero-helper', _emit(source, other, variants))]
        recovered = [_recover_copy(tree, fn, variants, aliases, words, params) for words in native]
        if all(row is not None for row in recovered):
            texts = [_emit(source, row, variants) for row in recovered]
            if all(row == texts[0] for row in texts):
                result.append(('rac3-native-quadword-read-before-clear', texts[0]))
        return result
    except Exception:
        # Parse extensions, unknown provenance and changed inputs fail closed.
        return []
