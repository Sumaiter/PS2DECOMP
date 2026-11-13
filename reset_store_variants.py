"""Rotate independent literal reset stores without changing their C semantics.

Only plain, unescaped object/byte-pointer parameters and constant disjoint spans
are accepted. No operation is moved across a call, read, aliasing store, or
qualified access. The real compiler and every native relocation still decide
whether an emitted variant matches; this module does not certify functions.
"""
import copy
import re

from search_rules import Variants


WIDTHS = {'s8 *': 1, 'u8 *': 1, 'char *': 1,
          's16 *': 2, 'u16 *': 2, 'short *': 2,
          's32 *': 4, 'u32 *': 4, 'int *': 4, 'unsigned int *': 4}
BYTE_POINTERS = {'s8 *', 'u8 *', 'char *'}


def generate(source, repository):
    rules = Variants(repository)
    try:
        tree, function = rules.parse(source)
    except Exception:
        return
    a = rules.a
    # WIDTHS describes the repository's common.h typedefs. A source-local
    # typedef could shadow those spellings (EE long is not a 32-bit word).
    # Infer no alternative layout: conservatively reject all local typedefs.
    if any(isinstance(n, a.Typedef) for *_, n in rules._nodes(tree)):
        return
    nodes = list(rules._nodes(function))
    # Taking an argument's address, shadowing it, or qualifying access makes
    # this intentionally small proof inapplicable.
    if any(getattr(n, 'quals', None) for *_, n in nodes):
        return
    escaped = {n.expr.name for *_, n in nodes
               if isinstance(n, a.UnaryOp) and n.op == '&'
               and isinstance(n.expr, a.ID)}
    declarations = {}
    for *_, n in nodes:
        if isinstance(n, a.Decl) and n.name:
            declarations.setdefault(n.name, []).append(n)
    parameters = function.decl.type.args.params if function.decl.type.args else []
    bases = {d.name for d in parameters if isinstance(d, a.Decl)
             and d.name not in escaped and len(declarations.get(d.name, [])) == 1
             and not d.storage and rules.generator.visit(d.type)
             in BYTE_POINTERS | {'void *'}}
    # An assignment can change the parameter's address or expose its storage.
    bases -= {n.lvalue.name for *_, n in nodes
              if isinstance(n, a.Assignment) and isinstance(n.lvalue, a.ID)}
    bases -= {n.expr.name for *_, n in nodes
              if isinstance(n, a.UnaryOp) and n.op in ('p++', 'p--', '++', '--')
              and isinstance(n.expr, a.ID)}

    def integer(n):
        if isinstance(n, a.Constant) and n.type in ('int', 'unsigned int'):
            token = re.sub(r'[uUlL]+$', '', n.value)
            try:
                return int(token, 16 if token.lower().startswith('0x') else
                           8 if len(token) > 1 and token.startswith('0') else 10)
            except ValueError:
                return None
        if isinstance(n, a.UnaryOp) and n.op in ('+', '-'):
            value = integer(n.expr)
            return None if value is None else value if n.op == '+' else -value
        return None

    def span(statement):
        if not isinstance(statement, a.Assignment) or statement.op != '=':
            return None
        if integer(statement.rvalue) is None:
            return None
        left = statement.lvalue
        if not isinstance(left, a.UnaryOp) or left.op != '*' or not isinstance(left.expr, a.Cast):
            return None
        cast = left.expr
        spelling = rules.generator.visit(cast.to_type.type)
        if spelling not in WIDTHS or any(getattr(n, 'quals', None) for *_, n in rules._nodes(cast)):
            return None
        pointer, offset = cast.expr, 0
        if isinstance(pointer, a.BinaryOp) and pointer.op == '+':
            offset = integer(pointer.right)
            pointer = pointer.left
            if offset is None:
                return None
        byte_cast = False
        if isinstance(pointer, a.Cast):
            if rules.generator.visit(pointer.to_type.type) not in BYTE_POINTERS:
                return None
            if any(getattr(n, 'quals', None) for *_, n in rules._nodes(pointer)):
                return None
            pointer, byte_cast = pointer.expr, True
        if not isinstance(pointer, a.ID) or pointer.name not in bases:
            return None
        decl = declarations[pointer.name][0]
        if rules.generator.visit(decl.type) == 'void *' and not byte_cast:
            return None
        if offset < 0 or offset + WIDTHS[spelling] > 0x100000:
            return None
        return (pointer.name, offset, offset + WIDTHS[spelling],
                spelling, rules.generator.visit(statement.rvalue))

    original_nodes = list(rules._nodes(function.body))
    emitted = set()
    for position, (*_, block) in enumerate(original_nodes):
        if not isinstance(block, a.Compound):
            continue
        statements = block.block_items or []
        begin = 0
        while begin < len(statements):
            spans, end = [], begin
            while end < len(statements):
                value = span(statements[end])
                if value is None or spans and (value[0] != spans[0][0] or any(
                        not (value[2] <= old[1] or old[2] <= value[1]) for old in spans)):
                    break
                spans.append(value)
                end += 1
            if len(spans) >= 3:
                # A full independent reset block may interleave different
                # literal values/widths. Only equal *typed literal* endpoints
                # are rotated; all crossed stores remain disjoint and pure.
                for i, value in enumerate(spans):
                    # One candidate for each reset run, rather than every
                    # suffix of a large identical run. This changes candidate
                    # selection only; the application's budgets stay intact.
                    if i and spans[i - 1][3:] == value[3:]:
                        continue
                    later = [j for j in range(i + 2, len(spans))
                             if spans[j][3:] == value[3:]]
                    if not later:
                        continue
                    j = later[-1]
                    other = copy.deepcopy(tree)
                    target = next(n for n in other.ext if isinstance(n, a.FuncDef))
                    changed = list(rules._nodes(target.body))[position][3]
                    moved = changed.block_items.pop(begin + i)
                    changed.block_items.insert(begin + j, moved)
                    text = rules._emit(source, other)
                    if text not in emitted:
                        emitted.add(text)
                        yield 'disjoint-literal-reset-rotation', text
            begin = max(begin + 1, end)
