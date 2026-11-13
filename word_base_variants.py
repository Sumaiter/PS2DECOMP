"""Native-gated split of an existing typed word address into base and index.

No pointer type is inferred: the destination and final cast must already be
the same plain 32-bit word pointer. A retail straight-line prefix must prove
the parameter, field pointer, constant base adjustment and four-byte stride
at EVERY supplied placement. No function address or ABI special case exists.
"""
import copy
import re
import struct

from search_rules import Variants


WORD_POINTERS = {'s32 *', 'u32 *', 'int *', 'unsigned int *'}
WORD_INTS = {'s32', 'u32', 'int', 'unsigned int'}
BYTE_POINTERS = {'u8 *', 's8 *', 'char *', 'unsigned char *'}


def _native_prefix(body, object_register, index_register, field, offset):
    """Prove the existing native address without reading another function."""
    if not isinstance(body, bytes) or len(body) % 4:
        return False
    state = {object_register: ('object',), index_register: ('index',)}
    for (word,) in struct.iter_unpack('<I', body):
        op, rs, rt, rd, sh, fn = (word >> 26, (word >> 21) & 31,
                                (word >> 16) & 31, (word >> 11) & 31,
                                (word >> 6) & 31, word & 63)
        imm = word & 0xffff
        imm = imm - 0x10000 if imm & 0x8000 else imm
        left, right = state.get(rs), state.get(rt)
        # Only the unconditional entry prefix is interpreted. Never cross a
        # branch, call, likely-delay slot or unknown control-flow edge.
        if op in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23) or (op == 0 and fn in (8, 9, 12, 13)):
            return False
        if op == 0 and fn == 0:  # SLL, native 32-bit byte displacement
            if rd:
                state[rd] = ('scaled',) if right == ('index',) and sh == 2 else None
        elif op == 35:  # LW, not an inferred wider load
            state[rt] = ('field',) if left == ('object',) and imm == field else None
        elif op == 9:  # ADDIU, exact field-base displacement
            state[rt] = ('base',) if left == ('field',) and imm == offset else None
        elif op == 0 and fn in (33, 45):
            if rd and left == ('base',) and right == ('scaled',):
                return True
            state[rd] = left if rt == 0 else right if rs == 0 else None
        elif op in (40, 41, 43, 63):
            pass  # stores do not change the symbolic integer registers
        elif op == 0:
            if rd:
                state[rd] = None
        elif op == 17:
            return False  # Includes MFC1/CFC1 writes and FP argument layouts.
        else:  # Conservatively invalidate an unknown integer result.
            state[rt] = None
    return False


def generate(source, repository, native_bodies=()):
    if re.search(r'\b(?:__LINE__|__FILE__|__COUNTER__)\b',source):
        return
    rules = Variants(repository)
    try:
        tree, function = rules.parse(source)
    except Exception:
        return
    native_bodies = tuple(native_bodies)
    a = rules.a
    all_nodes = list(rules._nodes(tree))
    nodes = list(rules._nodes(function))
    if (not native_bodies or any(isinstance(n, a.Typedef) for *_, n in all_nodes)
            or any(getattr(n, 'quals', None) for *_, n in all_nodes)
            or any(isinstance(n, (a.Goto, a.Label)) for *_, n in nodes)):
        return
    params = function.decl.type.args.params if function.decl.type.args else []
    # Only the directly known integer/pointer argument registers are accepted.
    if not 1 <= len(params) <= 4 or any(not isinstance(p, a.Decl) or
            rules.generator.visit(p.type) not in WORD_INTS | {'void *'} | BYTE_POINTERS
            for p in params):
        return
    declarations = {}
    for *_, n in nodes:
        if isinstance(n, a.Decl) and n.name:
            declarations.setdefault(n.name, []).append(n)
    if any(len(v) != 1 for v in declarations.values()):
        return
    escaped = {n.expr.name for *_, n in nodes if isinstance(n, a.UnaryOp)
               and n.op == '&' and isinstance(n.expr, a.ID)}
    modified = {n.lvalue.name for *_, n in nodes if isinstance(n, a.Assignment)
                and isinstance(n.lvalue, a.ID)}
    modified |= {n.expr.name for *_, n in nodes if isinstance(n, a.UnaryOp)
                 and n.op in ('++', '--', 'p++', 'p--') and isinstance(n.expr, a.ID)}
    parameter_index = {p.name: i for i, p in enumerate(params)}
    plain_params = set(parameter_index) - escaped - modified

    def integer(node):
        if not isinstance(node, a.Constant) or node.type not in ('int', 'unsigned int'):
            return None
        token = re.sub('[uUlL]+$', '', node.value)
        try:
            return int(token, 16 if token.lower().startswith('0x') else
                       8 if len(token) > 1 and token.startswith('0') else 10)
        except ValueError:
            return None

    def word_index(node):
        if not isinstance(node, a.BinaryOp) or node.op != '*' or integer(node.right) != 4:
            return None
        index = node.left
        if (not isinstance(index, a.ID) or index.name not in plain_params
                or rules.generator.visit(params[parameter_index[index.name]].type) not in WORD_INTS):
            return None
        return index

    statements = function.body.block_items or []
    for position, statement in enumerate(statements):
        if not isinstance(statement, a.Assignment) or statement.op != '=' or not isinstance(statement.lvalue, a.ID):
            continue
        decls = declarations.get(statement.lvalue.name, [])
        cast = statement.rvalue
        if (len(decls) != 1 or not isinstance(cast, a.Cast)
                or decls[0] not in statements
                or rules.generator.visit(cast.to_type.type) not in WORD_POINTERS
                or rules.generator.visit(decls[0].type) != rules.generator.visit(cast.to_type.type)
                or statement.lvalue.name in escaped or decls[0].storage):
            continue
        addition = cast.expr
        if not isinstance(addition, a.BinaryOp) or addition.op != '+':
            continue
        base, scale = addition.left, addition.right
        if not isinstance(base, a.BinaryOp) or base.op != '+':
            continue
        offset = integer(base.right)
        if offset is None or not 0 <= offset <= 0x7fff or offset % 4:
            continue
        relay = None
        if isinstance(scale, a.ID):
            # Resolve one private, unescaped, uniquely assigned scalar relay.
            matches = [s for s in statements[:position] if isinstance(s, a.Assignment)
                       and s.op == '=' and isinstance(s.lvalue, a.ID) and s.lvalue.name == scale.name]
            uses = [n for *_, n in nodes if isinstance(n, a.ID) and n.name == scale.name]
            ds = declarations.get(scale.name, [])
            if (len(matches) != 1 or len(uses) != 2 or len(ds) != 1
                    or ds[0] not in statements
                    or ds[0].init is not None or ds[0].storage or scale.name in escaped
                    or rules.generator.visit(ds[0].type) not in WORD_INTS):
                continue
            scaled = matches[0].rvalue
            relay = scale.name
        else:
            scaled = scale
        index = word_index(scaled)
        if index is None:
            continue
        byte_cast = base.left
        if (not isinstance(byte_cast, a.Cast)
                or rules.generator.visit(byte_cast.to_type.type) not in BYTE_POINTERS):
            continue
        load = byte_cast.expr
        if not isinstance(load, a.UnaryOp) or load.op != '*' or not isinstance(load.expr, a.Cast):
            continue
        if rules.generator.visit(load.expr.to_type.type) != 'void **':
            continue
        address = load.expr.expr
        if not isinstance(address, a.BinaryOp) or address.op != '+':
            continue
        field = integer(address.right)
        object_cast = address.left
        if (field is None or not 0 <= field <= 0x7fff or field % 4
                or not isinstance(object_cast, a.Cast)
                or rules.generator.visit(object_cast.to_type.type) not in BYTE_POINTERS
                or not isinstance(object_cast.expr, a.ID)
                or object_cast.expr.name not in plain_params
                or rules.generator.visit(params[parameter_index[object_cast.expr.name]].type)
                not in {'void *'} | BYTE_POINTERS):
            continue
        if not all(_native_prefix(body, 4 + parameter_index[object_cast.expr.name],
                                  4 + parameter_index[index.name], field, offset)
                   for body in native_bodies):
            continue
        transformed = copy.deepcopy(tree)
        target_function = next(n for n in transformed.ext if isinstance(n, a.FuncDef))
        target_statements = target_function.body.block_items
        fresh = 'rac4_word_base'
        all_names = {n.name for *_, n in all_nodes if isinstance(n, (a.Decl, a.ID)) and n.name}
        while fresh in all_names:
            fresh += '_'
        base_decl = copy.deepcopy(decls[0])
        base_decl.name, base_decl.init = fresh, None
        type_decl = base_decl.type
        while not isinstance(type_decl, a.TypeDecl):
            type_decl = type_decl.type
        type_decl.declname = fresh
        target = target_statements[position]
        base_assign = a.Assignment('=', a.ID(fresh), a.Cast(copy.deepcopy(cast.to_type), copy.deepcopy(base)))
        target.rvalue = a.BinaryOp('+', a.ID(fresh), copy.deepcopy(index))
        target_statements.insert(position, base_assign)
        first_statement = next((i for i, n in enumerate(target_statements) if not isinstance(n, a.Decl)), len(target_statements))
        target_statements.insert(first_statement, base_decl)
        # The proven private multiplication relay has no observations left.
        # Remove its declaration/assignment, avoiding a redundant signed
        # multiplication in C. No expression with side effects is discarded.
        if relay is not None:
            target_function.body.block_items = [n for n in target_statements
                if not (isinstance(n, a.Decl) and n.name == relay)
                and not (isinstance(n, a.Assignment) and isinstance(n.lvalue, a.ID)
                         and n.lvalue.name == relay)]
        yield 'belegter-word-array-basis-vor-index', rules._emit(source, transformed)
        return
