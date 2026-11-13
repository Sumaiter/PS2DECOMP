"""Retarget an unobservable same-typed local definition to its sole consumer.

    temporary = expression;
    ... independent plain local assignments ...
    target = pure_expression(temporary);

becomes a definition of target and a self-updating pure expression. Assignment
conversions and evaluation order are retained. This module never infers a type,
changes an ABI/number/operation, or uses native names/addresses. It emits one
ordinary C search variant and provides no match or semantic certificate.
"""
import copy
import re

LABEL = 'private-scalar-definition-retarget'


def children(source, variants):
    generated = generate(variants, source)
    return [(LABEL, generated)] if generated is not None else []


def generate(rules, source):
    if not callable(getattr(rules, 'plain_header', None)) or not rules.plain_header():
        return None
    # Keep this boundary explicit even if a future caller supplies a parser
    # with weaker directive checks. Foreign headers can hide volatile types.
    clean = re.sub(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|/\*.*?\*/|//[^\n]*',
                   lambda m: m[1] if m[1] else ' ', source, flags=re.S)
    directives = re.findall(r'^\s*#.*$', clean, re.M)
    if ('\\\n' in source or '\\\r\n' in source
            or any(not re.fullmatch(r'\s*#\s*include\s*"common.h"\s*', line) for line in directives)):
        return None
    flags = re.findall(r'/\*\s*cflags\b.*?\*/', source, re.S)
    if flags and (len(flags) != 1 or source.splitlines()[0] != flags[0]
                  or not re.fullmatch(r'/\* cflags: [^\r\n]* \*/', flags[0])):
        return None
    a = rules.a
    try:
        tree, fn = copy.deepcopy(rules.parse(source))
    except Exception:
        return None
    nodes = [n for *_, n in rules._nodes(tree)]
    body_nodes = [n for *_, n in rules._nodes(fn.body)]
    for n in nodes:
        if isinstance(n, a.Typedef) or any(getattr(n, key, None) for key in
                ('quals', 'align', 'gcc_attributes', 'asmlabel')):
            return None
        if 'register' in getattr(n, 'storage', []):
            return None
        name = getattr(n, 'name', None)
        if isinstance(name, str) and (name.startswith('__') or name in {'asm', '_asm'}):
            return None
        if 'asm' in type(n).__name__.lower():
            return None
    if any(isinstance(n, (a.Goto, a.Label, a.FuncCall)) for n in body_nodes):
        return None
    try:
        definitions = [n for n in rules.parser().parse(rules.prelude).ext if isinstance(n, a.Typedef)]
    except Exception:
        return None
    aliases = {n.name: n for n in definitions}
    if len(aliases) != len(definitions):
        return None
    primitive_leaves = {'int', 'signed', 'signed int', 'unsigned', 'unsigned int', 'float', 'double'}

    def primitive(ty, seen=frozenset()):
        if (not isinstance(ty, a.TypeDecl) or ty.quals
                or any(getattr(ty, key, None) for key in ('align', 'gcc_attributes', 'asmlabel'))
                or not isinstance(ty.type, a.IdentifierType)):
            return None
        names = ty.type.names
        if len(names) == 1 and names[0] in aliases:
            name = names[0]
            alias = aliases[name]
            if name in seen or alias.quals or any(getattr(alias, key, None) for key in ('align', 'gcc_attributes', 'asmlabel')):
                return None
            return primitive(alias.type, seen | {name})
        spelling = ' '.join(names)
        return spelling if spelling in primitive_leaves else None
    decls = [n for n in nodes if isinstance(n, a.Decl) and n.name]
    counts = {}
    for n in decls:
        counts[n.name] = counts.get(n.name, 0) + 1
    local_decls = {n.name: n for n in fn.body.block_items or [] if isinstance(n, a.Decl)}

    def scalar_type(decl):
        ty = decl.type
        if (decl.storage or decl.quals or decl.init is not None
                or not isinstance(ty, a.TypeDecl) or ty.quals
                or not isinstance(ty.type, a.IdentifierType)):
            return None
        # Alias names carry no width or game identity. Resolve both locals
        # through this project's actual, unqualified header definitions.
        return primitive(ty)

    local_types = {name: scalar_type(decl) for name, decl in local_decls.items()}
    local_types = {name: ty for name, ty in local_types.items() if ty and counts[name] == 1}
    all_types = dict(local_types)
    args = fn.decl.type.args
    for p in args.params if args else []:
        if isinstance(p, a.Decl) and isinstance(p.type, a.TypeDecl) and isinstance(p.type.type, a.IdentifierType):
            resolved = primitive(p.type)
            if counts.get(p.name) == 1 and resolved is not None:
                all_types[p.name] = resolved

    def mentions(node, name):
        return [n for *_, n in rules._nodes(node) if isinstance(n, a.ID) and n.name == name]

    def pure(node):
        if isinstance(node, a.ID):
            return node.name in all_types
        if isinstance(node, a.Constant):
            return True
        if isinstance(node, a.UnaryOp) and node.op in ('+', '-', '!', '~'):
            return pure(node.expr)
        if isinstance(node, a.BinaryOp) and node.op in ('+', '-', '*', '/', '%', '&', '|', '^', '<<', '>>'):
            return pure(node.left) and pure(node.right)
        return False

    items = fn.body.block_items or []
    for i, first in enumerate(items):
        if not isinstance(first, a.Assignment) or first.op != '=' or not isinstance(first.lvalue, a.ID):
            continue
        temporary = first.lvalue.name
        if temporary not in local_types or mentions(first.rvalue, temporary):
            continue
        # No local address escape anywhere, even after the rewritten pair.
        if any(isinstance(n, a.UnaryOp) and n.op == '&' and mentions(n.expr, temporary) for n in body_nodes):
            continue
        for j in range(i + 1, len(items)):
            second = items[j]
            if not isinstance(second, a.Assignment) or second.op != '=' or not isinstance(second.lvalue, a.ID):
                break
            target = second.lvalue.name
            if not mentions(second.rvalue, temporary):
                # These assignments stay in place. Their local destinations
                # may be pointers (for example an independent array cursor).
                # Neither rewritten local can escape or occur in the run.
                decl = local_decls.get(target)
                if (decl is None or decl.storage or decl.quals or decl.init is not None
                        or counts.get(target) != 1 or mentions(second, temporary)):
                    break
                continue
            if (target == temporary or target not in local_types
                    or local_types[target] != local_types[temporary]
                    or mentions(second.rvalue, target) or not pure(second.rvalue)
                    or mentions(first.rvalue, target)):
                break
            if any(isinstance(n, a.UnaryOp) and n.op == '&' and mentions(n.expr, target) for n in body_nodes):
                break
            # The temporary's only IDs are its original lhs and this one rhs.
            permitted = {id(first.lvalue)} | {id(n) for n in mentions(second.rvalue, temporary)}
            if any(id(n) not in permitted for n in mentions(fn.body, temporary)):
                break
            # Retargeting cannot overwrite an earlier consumer of target, nor
            # cross an intervening target definition or reference.
            if any(mentions(item, target) for item in items[:j]):
                break
            if any(not isinstance(item, (a.Decl, a.Assignment)) for item in items[:j]):
                break
            first.lvalue.name = target
            for n in mentions(second.rvalue, temporary):
                n.name = target
            generated = rules._emit(source, tree)
            if re.findall(r'/\*\s*cflags\b.*?\*/', generated, re.S) != flags:
                return None
            return generated
    return None
