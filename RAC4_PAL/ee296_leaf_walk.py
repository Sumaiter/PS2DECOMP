"""One unchanged-source EE296 alternative for a proved read-only pointer loop.

This is a deliberately small search domain, not compiler attribution. Calls,
stores, absolute/global addresses, FP/VU/stack/GP code and other loop forms are
outside the evidence. Every placement must agree. No source types are inferred.
"""
import re
import struct

from ee_common import complete_cfg
from search_rules import code_only
from . import ee296_profile


def analyze(rules, source):
    """Source-only gate: run before reading a claim, native ELF or compiler."""
    if (not isinstance(source, str) or not callable(getattr(rules, 'plain_header', None)) or not rules.plain_header()
            or re.search(r'/\*\s*cflags\b', source)
            or re.search(r'\b(?:volatile|asm|__\w+)\b', code_only(source))):
        return None
    if [line.strip() for line in re.findall(r'^\s*#.*$', code_only(source), re.M)] != ['#include "common.h"']:
        return None
    try:
        from m2c_pycparser.plyparser import ParseError
        tree, fn = rules.parse(source)
        a = rules.a
        nodes = [n for *_, n in rules._nodes(tree)]
        if len(tree.ext) != 1 or tree.ext[0] is not fn:
            return None
        if any(any(getattr(n, key, None) for key in ('quals', 'align', 'funcspec', 'gcc_attributes', 'asmlabel', 'storage')) for n in nodes):
            return None
        header = rules.parser().parse(rules.prelude).ext
        if (any(not isinstance(n, a.Typedef) for n in header)
                or len({n.name for n in header}) != len(header)
                or any(any(getattr(n, key, None) for key in ('quals', 'align', 'funcspec', 'gcc_attributes', 'asmlabel'))
                       for *_, n in rules._nodes(rules.parser().parse(rules.prelude)))):
            return None
        aliases = {n.name: n.type for n in header}

        def scalar(node, seen=frozenset()):
            if (not isinstance(node, a.TypeDecl) or not isinstance(node.type, a.IdentifierType)
                    or any(getattr(node, key, None) for key in ('quals', 'align', 'gcc_attributes', 'asmlabel'))):
                return None
            names = node.type.names
            if len(names) == 1 and names[0] in aliases:
                return None if names[0] in seen else scalar(aliases[names[0]], seen | {names[0]})
            return ' '.join(names)

        def void_pointer(node):
            return isinstance(node, a.PtrDecl) and scalar(node.type) == 'void'

        if not void_pointer(fn.decl.type.type):
            return None
        params = fn.decl.type.args.params if fn.decl.type.args else []
        if len(params) != 1 or not isinstance(params[0], a.Decl) or not void_pointer(params[0].type):
            return None
        declarations = [n for n in nodes if isinstance(n, a.Decl) and n is not fn.decl and n not in params]
        if not declarations or any(not void_pointer(n.type) for n in declarations):
            return None
        names = [params[0].name, *[n.name for n in declarations]]
        if None in names or len(set(names)) != len(names):
            return None
        allowed_ids = {*names, 'NULL'}
        if any(isinstance(n, a.ID) and n.name not in allowed_ids for n in nodes):
            return None
        loops = [n for n in nodes if isinstance(n, (a.DoWhile, a.While, a.For))]
        returns = [n for n in nodes if isinstance(n, a.Return)]
        if (len(loops) != 1 or not isinstance(loops[0], a.DoWhile) or len(returns) != 1
                or not isinstance(returns[0].expr, a.ID) or returns[0].expr.name not in names):
            return None

        def integer(node):
            sign = 1
            if isinstance(node, a.UnaryOp) and node.op in ('+', '-'):
                sign = -1 if node.op == '-' else 1
                node = node.expr
            if not isinstance(node, a.Constant) or node.type not in ('int', 'unsigned int'):
                return None
            value = re.sub(r'[uUlL]+$', '', node.value)
            try:
                return sign * int(value, 8 if re.fullmatch(r'0[0-7]+', value) else 0)
            except ValueError:
                return None

        def null(node):
            return isinstance(node, a.ID) and node.name == 'NULL' or integer(node) == 0

        def base_pointer(node):
            if isinstance(node, a.ID):
                return node.name in names
            return (isinstance(node, a.Cast) and isinstance(node.to_type.type, a.PtrDecl)
                    and scalar(node.to_type.type.type) in ('char', 'signed char', 'unsigned char', 'void')
                    and base_pointer(node.expr))

        loads = []

        def load(node):
            if not isinstance(node, a.UnaryOp) or node.op != '*' or not isinstance(node.expr, a.Cast):
                return False
            typ = node.expr.to_type.type
            if not isinstance(typ, a.PtrDecl):
                return False
            if not (void_pointer(typ.type) or scalar(typ.type) in ('int', 'signed int', 'unsigned int')):
                return False
            expr = node.expr.expr
            if base_pointer(expr):
                offset = 0
            elif isinstance(expr, a.BinaryOp) and expr.op == '+' and base_pointer(expr.left):
                offset = integer(expr.right)
            else:
                return False
            if offset is None or offset % 4 or not -32768 <= offset <= 32764:
                return False
            loads.append(offset)
            return True

        def value(node):
            return base_pointer(node) or null(node) or load(node)

        def condition(node):
            if isinstance(node, a.BinaryOp) and node.op in ('&&', '||'):
                return condition(node.left) and condition(node.right)
            if isinstance(node, a.BinaryOp) and node.op in ('==', '!='):
                return null(node.right) and value(node.left) or null(node.left) and value(node.right)
            return False

        def statement(node):
            if isinstance(node, a.Compound):
                return all(statement(n) for n in node.block_items or [])
            if isinstance(node, a.Decl):
                return node.init is None or value(node.init)
            if isinstance(node, a.Assignment):
                return node.op == '=' and isinstance(node.lvalue, a.ID) and node.lvalue.name in names[1:] and value(node.rvalue)
            if isinstance(node, a.If):
                return condition(node.cond) and statement(node.iftrue) and (node.iffalse is None or statement(node.iffalse))
            if isinstance(node, a.DoWhile):
                return condition(node.cond) and statement(node.stmt)
            if isinstance(node, a.Return):
                return isinstance(node.expr, a.ID) and node.expr.name in names
            return False

        if not statement(fn.body) or not loads:
            return None
        return dict(name=fn.decl.name, loadOffsets=sorted(loads))
    except (ValueError, TypeError, AttributeError, KeyError, ParseError):
        return None


def native_shape(raw, start):
    if (not isinstance(raw, (bytes, bytearray)) or isinstance(start, bool) or not isinstance(start, int)
            or start % 4 or not 0 <= start <= 0xffffffff - len(raw) or not complete_cfg(raw, start)):
        return None
    loads, backwards = [], 0
    for off, (w,) in enumerate(struct.iter_unpack('<I', raw)):
        if w == 0:
            continue
        op, rs, rt, rd, fn = w >> 26, w >> 21 & 31, w >> 16 & 31, w >> 11 & 31, w & 63
        if op == 35:
            if rs in (0, 1, 28, 29) or rt in (0, 1, 28, 29, 31):
                return None
            imm = (w & 0x7fff) - (w & 0x8000)
            if imm % 4:
                return None
            loads.append(imm)
        elif op in (4, 5, 20, 21):
            if rs in (1, 28, 29, 31) or rt in (1, 28, 29, 31) or 0 not in (rs, rt):
                return None
            if (w & 0x8000):
                if op not in (5, 21):
                    return None
                backwards += 1
        elif op == 0 and fn == 8:
            if w != 0x03e00008:
                return None
        elif op == 0 and fn in (33, 45):
            if rt != 0 or rs in (1, 28, 29, 31) or rd in (0, 1, 28, 29, 31):
                return None
        else:
            return None
    return dict(loadOffsets=sorted(loads)) if loads and backwards == 1 else None


def generate_for_engine(engine, name, source, *, rules=None):
    if rules is None:
        from search_rules import Variants
        rules = Variants(engine.root)
    shape = analyze(rules, source)
    if shape is None or shape['name'] != name:
        return []
    try:
        primary = tuple(engine.place(name)[:4])
        places = [tuple(p) for p in engine.all_places(name)]
        if not places or places[0] != primary or places.count(primary) != 1 or len(set(places)) != len(places):
            return []
        raw_first = None
        for place in places:
            if (len(place) != 4 or not all(isinstance(p, str) and p for p in place[:2])
                    or any(isinstance(p, bool) or not isinstance(p, int) for p in place[2:])
                    or place[3] <= 0 or place[3] % 4):
                return []
            if engine.assert_reference(place[0]) is not None:
                return []
            raw = engine.check.disc_bytes(place)
            native = native_shape(raw, place[2])
            if len(raw) != place[3] or native is None or native['loadOffsets'] != shape['loadOffsets']:
                return []
            if raw_first is not None and raw_first != raw:
                return []
            raw_first = raw
        hashes = ee296_profile.current_hashes(engine.root)
        timeout = ee296_profile.pinned_session(engine, name, hashes)
        status = ee296_profile.inspect(engine.root, timeout=min(15, timeout))
        if not status.get('available') or not status.get('abiVerified'):
            return []
        ee296_profile.pinned_session(engine, name, status['inputHashes'])
        return [('EE296: read-only pointer loop',
                 '/* cflags: ' + ' '.join(ee296_profile.FLAGS) + ' */\n' + source)]
    except (ValueError, TypeError, AttributeError, IndexError, KeyError, OSError):
        return []
