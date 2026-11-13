"""Coalesce one unescaped integer counter's conditional temporary relay.

    temporary = predicate1 ? counter + 1 : counter;
    ... statements not observing either integer local ...
    counter = predicate2 ? temporary + 1 : temporary;

The only emitted form updates counter under the same predicates. Calls, reads,
predicates and their evaluation counts retain their original source positions.
This is a C-local lifetime change, never a callee ABI or FP algebra inference.
"""
import copy

from search_rules import Variants


INTEGER_TYPES = {'s32', 'u32', 'int', 'unsigned int'}


def generate(source, repository):
    rules = Variants(repository)
    try:
        tree, function = rules.parse(source)
    except Exception:
        return
    a = rules.a
    if any(isinstance(n, a.Typedef) for *_, n in rules._nodes(tree)):
        return
    nodes = list(rules._nodes(function))
    if any(getattr(n, 'quals', None) for *_, n in nodes):
        return
    if any(isinstance(n, (a.Goto, a.Label, a.Switch, a.Case, a.Default)) for *_, n in nodes):
        return
    if any(isinstance(n, a.ID) and (n.name in ('setjmp', '_setjmp', 'longjmp')
            or n.name.startswith('__builtin_')) for *_, n in nodes):
        return
    declarations = {}
    for *_, n in nodes:
        if isinstance(n, a.Decl) and n.name:
            declarations.setdefault(n.name, []).append(n)
    parameters = {d.name for d in function.decl.type.args.params
                  if isinstance(d, a.Decl)} if function.decl.type.args else set()
    escaped = {n.expr.name for *_, n in nodes
               if isinstance(n, a.UnaryOp) and n.op == '&' and isinstance(n.expr, a.ID)}

    def local_type(name):
        rows = declarations.get(name, [])
        if name in parameters or name in escaped or len(rows) != 1:
            return None
        decl = rows[0]
        if decl.storage:
            return None
        spelling = rules.generator.visit(decl.type)
        return spelling if spelling in INTEGER_TYPES else None

    def identifiers(n):
        return {x.name for *_, x in rules._nodes(n) if isinstance(x, a.ID)}

    def increment(n):
        if not isinstance(n, a.BinaryOp) or n.op != '+' or not isinstance(n.left, a.ID):
            return None
        value = n.right
        # Do not substitute another literal type, cast, sign or arithmetic
        # operation. The exact original increment AST is retained below.
        if not isinstance(value, a.Constant) or value.type not in ('int', 'unsigned int'):
            return None
        token = value.value.lower().rstrip('ul')
        if token not in ('1', '0x1', '01'):
            return None
        return n.left.name

    body_nodes = list(rules._nodes(function.body))
    for position, (*_, block) in enumerate(body_nodes):
        if not isinstance(block, a.Compound):
            continue
        statements = block.block_items or []
        for i, first in enumerate(statements):
            if not isinstance(first, a.Assignment) or first.op != '=' or not isinstance(first.lvalue, a.ID):
                continue
            ternary = first.rvalue
            if not isinstance(ternary, a.TernaryOp) or not isinstance(ternary.iffalse, a.ID):
                continue
            counter = increment(ternary.iftrue)
            relay = first.lvalue.name
            if counter is None or counter != ternary.iffalse.name or counter == relay:
                continue
            spelling = local_type(counter)
            if spelling is None or local_type(relay) != spelling:
                continue
            if identifiers(ternary.cond) & {counter, relay}:
                continue
            for j in range(i + 1, len(statements)):
                last = statements[j]
                if not isinstance(last, a.Assignment) or last.op != '=' or not isinstance(last.lvalue, a.ID) or last.lvalue.name != counter:
                    # No branch, declaration, return or loop transfer can
                    # bypass the final update after exposing an early value.
                    if not isinstance(last, a.Assignment) or last.op != '=':
                        break
                    if identifiers(last) & {counter, relay}:
                        break
                    continue
                right = last.rvalue
                if not isinstance(right, a.TernaryOp) or not isinstance(right.iffalse, a.ID):
                    break
                if increment(right.iftrue) != relay or right.iffalse.name != relay:
                    break
                if identifiers(right.cond) & {counter, relay}:
                    break
                # The relay must be this assignment's private SSA temporary:
                # its only ID uses are the first LHS and the two final arms.
                if sum(isinstance(n, a.ID) and n.name == relay for *_, n in nodes) != 3:
                    break
                # Both exact increment expressions must have the same type
                # and spelling aside from their identically typed base local.
                expected = copy.deepcopy(right.iftrue)
                expected.left.name = counter
                if rules.generator.visit(expected) != rules.generator.visit(ternary.iftrue):
                    break
                other = copy.deepcopy(tree)
                target = next(n for n in other.ext if isinstance(n, a.FuncDef))
                changed = list(rules._nodes(target.body))[position][3]
                initial = changed.block_items[i]
                final = changed.block_items[j]
                initial_if = a.If(initial.rvalue.cond,
                    a.Compound([a.Assignment('=', a.ID(counter), initial.rvalue.iftrue)]), None)
                final_increment = final.rvalue.iftrue
                final_increment.left.name = counter
                final_if = a.If(final.rvalue.cond,
                    a.Compound([a.Assignment('=', a.ID(counter), final_increment)]), None)
                changed.block_items[i], changed.block_items[j] = initial_if, final_if
                yield 'unescaped-integer-counter-relay', rules._emit(source, other)
                break
