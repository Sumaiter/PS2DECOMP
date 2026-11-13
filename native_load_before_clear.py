"""Restore a word read before a byte clear only when retail proves that order.

This is a native-driven source repair, never a C alias-analysis assumption.
The scalar keeps exactly the existing word type and value. No memory access
is dropped, no caller contract changes, and no intervening C statement moves.
Call generate_all for the required independent agreement of every placement.
"""
import copy
import re
import struct

from auto_engine import complete_cfg
from search_rules import Variants, code_only
from native_guard_variants import _canonical, _pointer, _read, _integer


def _parts(word):
    return word >> 26, word >> 21 & 31, word >> 16 & 31, (word & 32767) - (word & 32768)


def _control(word):
    op, rs, rt, _ = _parts(word)
    return (op in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23)
            or op == 17 and rs == 8 or op == 0 and word & 63 in (8, 9))


def _writes(word):
    """Conservative GPR write set; unknown encodings invalidate provenance."""
    op, rs, rt, _ = _parts(word)
    if word == 0:
        return set()
    if op == 0:
        fn = word & 63
        if fn in (0, 2, 3, 4, 6, 7, 16, 18, 20, 22, 23, 32, 33, 34, 35,
                  36, 37, 38, 39, 42, 43, 44, 45, 46, 47, 56, 58, 59, 60, 62, 63):
            return {word >> 11 & 31}
        if fn in (8, 12, 13, 17, 19, 24, 25, 26, 27):
            return set()
        return set(range(1, 32))
    if op == 3:
        return set(range(1, 16)) | {24, 25, 31}  # EABI preserved S-registers.
    if op in (1, 2, 4, 5, 6, 7, 20, 21, 22, 23, 31, 40, 41, 42, 43, 44, 45, 46, 49, 57, 62, 63):
        return set()
    if op in (8, 9, 10, 11, 12, 13, 14, 15, 24, 25, 26, 27, 30, 32, 33, 34, 35, 36, 37, 38, 39, 55):
        return {rt}
    if op == 17:
        return {rt} if rs in (0, 2) else set()
    return set(range(1, 32))


def _root(words, index, register):
    if register in (0, 29, 31):
        return False
    if register == 4:
        return all(register not in _writes(w) for w in words[:index])
    if register not in range(16, 24):
        return False
    for i, word in enumerate(words[:index]):
        if _control(word):
            return False
        # Immutable argument-zero alias established before the first split or
        # call; any later write on any path conservatively rejects this proof.
        if (word >> 26 == 0 and word & 63 in (37, 45)
                and word >> 11 & 31 == register and word >> 6 & 31 == 0
                and {word >> 21 & 31, word >> 16 & 31} == {0, 4}):
            return (all(4 not in _writes(w) for w in words[:i])
                    and all(register not in _writes(w) for w in words[i + 1:index]))
    return False


def _native(raw, start, pointer_offset, value_offset, clear_offset, destination_offset):
    if (not isinstance(raw, (bytes, bytearray)) or not 24 <= len(raw) <= 32768
            or len(raw) % 4 or not isinstance(start, int) or isinstance(start, bool)
            or start % 4 or not 0 <= start <= 0xffffffff - len(raw)
            or not complete_cfg(raw, start)):
        return False
    words = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
    matches = 0
    for i in range(1, len(words) - 3):
        op, pointer, value, displacement = _parts(words[i])
        co, root, zero, clear = _parts(words[i + 1])
        # The observed word is consumed either immediately after the byte
        # clear or as the JAL delay-slot store (which runs before the call).
        j = i + 2
        if words[j] >> 26 == 3:
            j += 1
        so, destination_base, stored, destination = _parts(words[j])
        if (op != 35 or displacement != value_offset or value in (0, 29, 31)
                or co != 40 or zero != 0 or clear != clear_offset
                or so != 43 or stored != value or destination != destination_offset
                or destination_base != root or pointer in (0, root, value, 29, 31)
                or not _root(words, i, root) or _control(words[i - 1])):
            continue
        # Find the last definition of the pointer, without crossing a control
        # edge/call. Intervening instructions may not mutate it. A disjoint
        # halfword store belongs to the existing preceding C statement and
        # is never crossed by the emitted C repair.
        proved = False
        for k in range(i - 1, max(-1, i - 7), -1):
            word = words[k]
            if _control(word):
                break
            po, base, target, field = _parts(word)
            if pointer in _writes(word):
                proved = (po == 35 and target == pointer and base == root
                          and field == pointer_offset
                          and (k == 0 or not _control(words[k - 1])))
                break
            if po in (40, 41, 43, 63, 31):
                if not (po == 41 and base == root
                        and all(field + 2 <= offset or offset + width <= field
                                for offset, width in ((pointer_offset, 4),
                                                      (clear_offset, 1),
                                                      (destination_offset, 4)))):
                    break
            elif po not in (0, 12):
                break
        matches += int(proved)
    return matches == 1


def generate(source, repository, raw, start):
    """One placement's proof. Prefer generate_all in application dispatch."""
    try:
        rules = Variants(repository)
        a = rules.a
        tree, fn = rules.parse(source)
        nodes = list(rules._nodes(fn))
        aliases = {n.name: n.type for n in rules.parser().parse(rules.prelude).ext
                   if isinstance(n, a.Typedef)}
        if (any(isinstance(n, a.Typedef) and n.name in aliases for n in tree.ext)
                or any(isinstance(n, (a.Typedef, a.Goto, a.Label)) for *_, n in nodes)
                or any(getattr(n, 'quals', ()) or getattr(n, 'storage', ())
                       or getattr(n, 'gcc_attributes', ()) or getattr(n, 'asmlabel', None)
                       for *_, n in nodes)
                or re.search(r'\b(?:volatile|restrict|__asm__|asm)\b', code_only(source))):
            return
        params = fn.decl.type.args.params if fn.decl.type.args else []
        if not params or not isinstance(params[0], a.Decl) or not _pointer(params[0].type, a, aliases):
            return
        object_name = params[0].name
        declarations = [n for *_, n in nodes if isinstance(n, a.Decl) and n.name]
        if len({n.name for n in declarations}) != len(declarations):
            return
        # No rebinding/address escape of the root pointer, including one in a
        # nested branch. Runtime pointee aliasing remains explicitly allowed.
        if any(isinstance(n, a.Assignment) and isinstance(n.lvalue, a.ID)
               and n.lvalue.name == object_name for *_, n in nodes):
            return
        if any(isinstance(n, a.UnaryOp) and n.op in ('&', '++', '--', 'p++', 'p--')
               and isinstance(n.expr, a.ID) and n.expr.name == object_name for *_, n in nodes):
            return
        def root(read):
            return read and isinstance(read[1], a.ID) and read[1].name == object_name
        for *_, block in nodes:
            if not isinstance(block, a.Compound):
                continue
            items = block.block_items or []
            for index in range(len(items) - 1):
                clear, store = items[index:index + 2]
                if not all(isinstance(n, a.Assignment) and n.op == '=' for n in (clear, store)):
                    continue
                cr, dst = _read(clear.lvalue, a, aliases), _read(store.lvalue, a, aliases)
                if (not root(cr) or not root(dst) or _integer(clear.rvalue, a) != 0
                        or _canonical(cr[0], a, aliases) not in ('char', 'signed char', 'unsigned char')
                        or _canonical(dst[0], a, aliases) not in ('int', 'unsigned int')):
                    continue
                value_expr = store.rvalue
                if isinstance(value_expr, a.Cast):
                    if _canonical(value_expr.to_type.type, a, aliases) != _canonical(dst[0], a, aliases):
                        continue
                    value_expr = value_expr.expr
                value = _read(value_expr, a, aliases)
                if not value or _canonical(value[0], a, aliases) != _canonical(dst[0], a, aliases):
                    continue
                pointer_expr = value[1]
                pointer = _read(pointer_expr, a, aliases)
                if isinstance(pointer_expr, a.ID):
                    previous = items[index - 1] if index else None
                    own = [d for d in declarations if d.name == pointer_expr.name]
                    if (not isinstance(previous, a.Assignment) or previous.op != '='
                            or not isinstance(previous.lvalue, a.ID) or previous.lvalue.name != pointer_expr.name
                            or len(own) != 1 or own[0] not in (fn.body.block_items or [])
                            or not _pointer(own[0].type, a, aliases)
                            or any(isinstance(n, a.UnaryOp) and n.op == '&'
                                   and isinstance(n.expr, a.ID) and n.expr.name == pointer_expr.name
                                   for *_, n in nodes)):
                        continue
                    pointer = _read(previous.rvalue, a, aliases)
                if not root(pointer) or not _pointer(pointer[0], a, aliases):
                    continue
                po, vo, co, do = pointer[2], value[2], cr[2], dst[2]
                if (any(o % 4 for o in (po, vo, do))
                        or any(o <= co < o + 4 for o in (po, do))
                        or not _native(raw, start, po, vo, co, do)):
                    continue
                other = copy.deepcopy(tree)
                other_fn = next(n for n in other.ext if isinstance(n, a.FuncDef))
                # Locate the same compound by its index in the AST traversal.
                compounds = [n for *_, n in nodes if isinstance(n, a.Compound)]
                clone_blocks = [n for *_, n in rules._nodes(other_fn) if isinstance(n, a.Compound)]
                clone = clone_blocks[compounds.index(block)]
                used = set(re.findall(r'\b\w+\b', source)); serial = 0
                while f'rac4_native_value_{serial}' in used:
                    serial += 1
                name = f'rac4_native_value_{serial}'
                kind = copy.deepcopy(value[0]); kind.declname = name
                declaration = copy.deepcopy(params[0])
                declaration.name = name
                declaration.type = kind
                declaration.init = None
                declaration.quals = []
                declaration.storage = []
                declaration.funcspec = []
                other_fn.body.block_items.insert(0, declaration)
                assignment = a.Assignment('=', a.ID(name), copy.deepcopy(value_expr))
                original_store = clone.block_items[index + (1 if clone is other_fn.body else 0) + 1]
                original_store.rvalue = a.ID(name)
                clone.block_items.insert(index + (1 if clone is other_fn.body else 0), assignment)
                yield 'native-word-read-before-byte-clear', rules._emit(source, other)
                return
    except (ValueError, TypeError, AttributeError, IndexError, KeyError, OSError):
        return


def generate_all(source, repository, placements):
    """Fail closed unless every independent native placement agrees exactly."""
    outputs = []
    for raw, start in placements:
        rows = list(generate(source, repository, raw, start))
        if len(rows) != 1:
            return []
        outputs.append(rows[0])
    return [outputs[0]] if outputs and all(row == outputs[0] for row in outputs) else []
