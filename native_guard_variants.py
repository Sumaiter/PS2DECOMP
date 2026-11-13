"""Recover one pointer read proven unconditional by the native guard delay slot.

This is deliberately not a general C load-hoisting optimization.  A guarded
draft can omit a native read on its exit path.  Only the exact, independently
decoded native prefix authorizes restoring that read.  Addresses, offsets,
constants and names are never selected by a function-specific table.
"""
from __future__ import annotations

import copy
import re
import struct

from auto_engine import complete_cfg
from search_rules import Variants


def _integer(node, a):
    if not isinstance(node, a.Constant) or node.type not in ('int', 'unsigned int'):
        return None
    try:
        return int(re.sub(r'[uUlL]+$', '', node.value), 0)
    except ValueError:
        return None


def _canonical(node, a, typedefs, seen=frozenset()):
    if getattr(node, 'quals', ()):
        return None
    if not isinstance(node, a.TypeDecl) or not isinstance(node.type, a.IdentifierType):
        return None
    names = node.type.names
    if len(names) == 1 and names[0] in typedefs:
        if names[0] in seen:
            return None
        return _canonical(typedefs[names[0]], a, typedefs, seen | {names[0]})
    spelling = ' '.join(names)
    return spelling if spelling in {
        'void', 'char', 'signed char', 'unsigned char', 'short', 'unsigned short',
        'int', 'unsigned int', 'long', 'unsigned long', 'float', 'double',
    } else None


def _pointer(node, a, typedefs):
    return (isinstance(node, a.PtrDecl) and not node.quals
            and _canonical(node.type, a, typedefs) is not None)


def _byte_address(node, a, typedefs):
    """Return the untouched base expression and a literal byte displacement."""
    if not isinstance(node, a.BinaryOp) or node.op != '+':
        return None
    amount = _integer(node.right, a)
    cast = node.left
    if (amount is None or not 0 <= amount <= 0x7fff
            or not isinstance(cast, a.Cast) or cast.to_type.quals
            or not isinstance(cast.to_type.type, a.PtrDecl)
            or cast.to_type.type.quals
            or _canonical(cast.to_type.type.type, a, typedefs)
            not in ('char', 'signed char', 'unsigned char')):
        return None
    return cast.expr, amount


def _read(node, a, typedefs):
    if (not isinstance(node, a.UnaryOp) or node.op != '*'
            or not isinstance(node.expr, a.Cast) or node.expr.to_type.quals
            or not isinstance(node.expr.to_type.type, a.PtrDecl)
            or node.expr.to_type.type.quals):
        return None
    address = _byte_address(node.expr.expr, a, typedefs)
    return (node.expr.to_type.type.type, *address) if address else None


def _native_prefix(raw, start, guard_offset, guard_constant, field_offset, word_offset):
    """Prove the first split is BEQ/always-LW and its taken path just returns."""
    if (not isinstance(raw, (bytes, bytearray)) or not 32 <= len(raw) <= 256
            or len(raw) % 4 or not isinstance(start, int) or isinstance(start, bool)
            or not 0 <= start <= 0xffffffff - len(raw) or start % 4
            or not complete_cfg(raw, start)):
        return False
    words = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
    registers = {0: ('constant', 0), 4: ('argument', 0), 29: ('stack', 0)}
    byte_reads = 0

    def fields(word):
        return word >> 26, word >> 21 & 31, word >> 16 & 31, (word & 0x7fff) - (word & 0x8000)

    def return_only(index):
        # No additional data access, condition, call or side effect on the
        # skipped body path.  Restores must be stack-relative 64-bit loads.
        while index < len(words):
            word = words[index]
            op, rs, rt, displacement = fields(word)
            if word == 0x03e00008:
                if index + 2 != len(words):
                    return False
                tail = words[index + 1]
                to, ts, tt, ti = fields(tail)
                frame = -registers[29][1]
                return ((tail == 0 and frame == 0)
                        or (to == 9 and ts == tt == 29 and ti == frame and ti > 0))
            if not (word == 0 or (op == 55 and rs == 29 and rt in range(16, 24))
                    or (op == 55 and rs == 29 and rt == 31)):
                return False
            index += 1
        return False

    for index, word in enumerate(words):
        op, rs, rt, displacement = fields(word)
        if op == 4:
            if byte_reads != 1 or index + 2 >= len(words):
                return False
            operands = {registers.get(rs), registers.get(rt)}
            if operands != {('byte', guard_offset), ('constant', guard_constant)}:
                return False
            target = index + 1 + displacement
            if target <= index + 2 or not return_only(target):
                return False
            delay = words[index + 1]
            do, ds, dt, di = fields(delay)
            if (do != 35 or registers.get(ds) != ('argument', 0)
                    or dt in (0, 29, 31) or di != field_offset):
                return False
            # The following native word dereferences this loaded pointer, just
            # like the first body assignment in the C draft.  No inferred type
            # or arbitrary integer-to-pointer conversion is introduced.
            fo, fs, ft, fi = fields(words[index + 2])
            return fo == 35 and fs == dt and ft not in (0, 29, 31) and fi == word_offset
        if word == 0:
            continue
        if op == 36 and registers.get(rs) == ('argument', 0) and displacement == guard_offset:
            if rt in (0, 29, 31):
                return False
            registers[rt] = ('byte', guard_offset)
            byte_reads += 1
        elif op == 63 and registers.get(rs, (None,))[0] == 'stack' and rt in (*range(16, 24), 31):
            pass  # Saving preserved GPRs/RA does not inspect runtime object data.
        elif op == 9 and rs == rt == 29 and registers.get(29, (None,))[0] == 'stack':
            if displacement >= 0:
                return False
            registers[29] = ('stack', registers[29][1] + displacement)
        elif op == 9 and rs == 0 and rt not in (0, 29, 31):
            registers[rt] = ('constant', displacement)
        elif op == 15 and rs == 0 and rt not in (0, 29, 31):
            registers[rt] = ('constant', (word & 0xffff) << 16)
        elif op == 13 and registers.get(rs, (None,))[0] == 'constant' and rt not in (0, 29, 31):
            registers[rt] = ('constant', registers[rs][1] | (word & 0xffff))
        elif op == 0 and word & 63 in (37, 45):
            rd = word >> 11 & 31
            # Only actual moves: no address algebra, RA/SP mutation or opaque
            # register dependency is permitted before the first native split.
            if rd in (0, 29, 31) or word >> 6 & 31 or not (rs == 0 or rt == 0):
                return False
            original = rt if rs == 0 else rs
            if original not in registers:
                return False
            registers[rd] = registers[original]
        else:
            return False
    return False


def generate(source, repository, raw, start):
    """Yield at most one native-proven recovery, or nothing for ambiguity."""
    try:
        rules = Variants(repository)
        a = rules.a
        tree, fn = rules.parse(source)
        typedefs = {n.name: n.type for n in rules.parser().parse(rules.prelude).ext
                    if isinstance(n, a.Typedef)}
        # Source-local typedefs/global definitions complicate qualification,
        # shadowing and ownership; they remain manual cases.
        if any(not isinstance(n, a.FuncDef)
               and not (isinstance(n, a.Decl) and isinstance(n.type, a.FuncDecl))
               for n in tree.ext):
            return
        params = fn.decl.type.args.params if fn.decl.type.args else []
        if (len(params) != 1 or not isinstance(params[0], a.Decl)
                or not _pointer(params[0].type, a, typedefs)
                or _canonical(fn.decl.type.type, a, typedefs) != 'void'):
            return
        argument = params[0].name
        items = fn.body.block_items or []
        declarations = []
        for item in items:
            if not isinstance(item, a.Decl):
                break
            if (item.init is not None or item.storage or item.quals or item.bitsize
                    or not (_pointer(item.type, a, typedefs)
                            or _canonical(item.type, a, typedefs) is not None)):
                return
            declarations.append(item)
        if len(items) != len(declarations) + 1:
            return
        guard = items[-1]
        if (not isinstance(guard, a.If) or guard.iffalse is not None
                or not isinstance(guard.iftrue, a.Compound)
                or not isinstance(guard.cond, a.BinaryOp) or guard.cond.op != '!='):
            return
        constant = _integer(guard.cond.right, a)
        guard_read = _read(guard.cond.left, a, typedefs)
        if (constant is None or not 0 <= constant <= 255 or not guard_read
                or _canonical(guard_read[0], a, typedefs) != 'unsigned char'
                or not isinstance(guard_read[1], a.ID) or guard_read[1].name != argument):
            return
        guard_offset = guard_read[2]
        body = guard.iftrue.block_items or []
        if (not body or not isinstance(body[0], a.Assignment) or body[0].op != '='
                or not isinstance(body[0].lvalue, a.ID)):
            return
        locals_by_name = {d.name: d for d in declarations}
        if (len(locals_by_name) != len(declarations) or argument in locals_by_name
                or body[0].lvalue.name not in locals_by_name
                or _canonical(locals_by_name[body[0].lvalue.name].type, a, typedefs)
                not in ('int', 'unsigned int')):
            return
        word_read = _read(body[0].rvalue, a, typedefs)
        if not word_read or _canonical(word_read[0], a, typedefs) not in ('int', 'unsigned int'):
            return
        pointer_load = word_read[1]
        pointer_read = _read(pointer_load, a, typedefs)
        if (not pointer_read or not _pointer(pointer_read[0], a, typedefs)
                or not isinstance(pointer_read[1], a.ID) or pointer_read[1].name != argument):
            return
        field_offset = pointer_read[2]
        if field_offset % 4 or word_read[2] % 4 or field_offset <= guard_offset < field_offset + 4:
            return
        nodes = list(rules._nodes(fn.body))
        if (any(isinstance(n, a.Decl) and n not in declarations for *_, n in nodes)
                or any(getattr(n, 'quals', ()) for *_, n in nodes)
                or any(isinstance(n, a.Assignment) and isinstance(n.lvalue, a.ID)
                       and n.lvalue.name == argument for *_, n in nodes)
                or any(isinstance(n, a.UnaryOp) and n.op in ('&', '++', '--', 'p++', 'p--')
                       and isinstance(n.expr, a.ID) and n.expr.name == argument for *_, n in nodes)):
            return
        field_reads = guard_reads = 0
        for *_, node in nodes:
            read = _read(node, a, typedefs)
            if read and isinstance(read[1], a.ID) and read[1].name == argument:
                field_reads += read[2] == field_offset
                guard_reads += read[2] == guard_offset
        if field_reads != 1 or guard_reads != 1:
            return
        if not _native_prefix(raw, start, guard_offset, constant, field_offset, word_read[2]):
            return
        used = set(re.findall(r'\b[A-Za-z_]\w*\b', source))
        index = 0
        while f'rac4_native_pointer_{index}' in used:
            index += 1
        fresh = f'rac4_native_pointer_{index}'
        declaration = copy.deepcopy(declarations[0])
        declaration.name = fresh
        declaration.type = copy.deepcopy(pointer_read[0])
        cursor = declaration.type
        while isinstance(cursor, a.PtrDecl):
            cursor = cursor.type
        cursor.declname = fresh
        declaration.init = None
        declaration.quals = []
        declaration.storage = []
        declaration.funcspec = []
        needle = rules.generator.visit(pointer_load)
        copied, copied_fn = rules.parse(rules.generator.visit(tree))
        replaced = 0
        for parent, attr, child_index, node in list(rules._nodes(copied_fn.body)):
            if isinstance(node, a.UnaryOp) and rules.generator.visit(node) == needle:
                if child_index is None:
                    setattr(parent, attr, a.ID(fresh))
                else:
                    getattr(parent, attr)[child_index] = a.ID(fresh)
                replaced += 1
        if replaced != 1:
            return
        assignment = a.Assignment('=', a.ID(fresh), copy.deepcopy(pointer_load))
        copied_fn.body.block_items[len(declarations):len(declarations)] = [declaration, assignment]
        yield 'native-unconditional-pointer-load', rules._emit(source, copied)
    except (ValueError, TypeError, AttributeError, IndexError, KeyError, OSError):
        # Unsupported ASTs/headers/reference fragments are not speculative
        # recovery candidates and remain available for manual review.
        return
