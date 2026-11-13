"""Delay a private signed-int literal default until after an unchanged call.

This extends the production FP-only postcall rule to an independently guarded
signed-int case. It never changes a call declaration or infers a native ABI.
"""
import copy
import re
from search_rules import Variants, code_only


def generate(source, repository):
    text = code_only(source)
    # Do not let a future permissive parser discard foreign directives, or let
    # emission activate a misplaced compiler profile comment.
    directives = re.findall(r'^\s*#.*$', text, re.M)
    if ('\\\n' in source or '\\\r\n' in source
            or any(not re.fullmatch(r'\s*#\s*include\s*"common.h"\s*', line) for line in directives)):
        return
    flags = re.findall(r'/\*\s*cflags\b.*?\*/', source, re.S)
    if flags and (len(flags) != 1 or source.splitlines()[0] != flags[0]
                  or not re.fullmatch(r'/\* cflags: [^\r\n]* \*/', flags[0])):
        return
    if re.search(r'\b(?:typedef|register|asm|__asm|__asm__|__builtin_[A-Za-z0-9_]*)\b', text):
        return
    rules = Variants(repository)
    if not callable(getattr(rules, 'plain_header', None)) or not rules.plain_header():
        return
    try:
        tree, fn = rules.parse(source)
    except Exception:
        return
    a = rules.a
    nodes = [n for *_, n in rules._nodes(tree)]
    # Never copy or move extension-bearing declarations or expressions. Hidden
    # cleanup/alignment/register-label behavior is outside this scalar rule.
    if any(getattr(n, field, None) for n in nodes
           for field in ('align', 'gcc_attributes', 'asmlabel', 'funcspec')):
        return
    if any(isinstance(n, (a.Goto, a.Label)) for n in nodes):
        return
    if any(q in ('volatile', '_Atomic') for n in nodes for q in getattr(n, 'quals', [])):
        return
    try:
        definitions = [n for n in rules.parser().parse(rules.prelude).ext
                       if isinstance(n, a.Typedef)]
    except Exception:
        return
    aliases = {n.name: n for n in definitions}
    if len(aliases) != len(definitions):
        return

    def plain_type_node(t):
        return not any(getattr(t, field, None) for field in
                       ('quals', 'align', 'gcc_attributes', 'asmlabel', 'funcspec'))

    def signed_int(t, seen=frozenset()):
        if (not isinstance(t, a.TypeDecl) or not plain_type_node(t)
                or not isinstance(t.type, a.IdentifierType)):
            return False
        names = t.type.names
        if len(names) == 1 and names[0] in aliases:
            name = names[0]
            alias = aliases[name]
            if name in seen or not plain_type_node(alias):
                return False
            return signed_int(alias.type, seen | {name})
        return ' '.join(names) in ('int', 'signed', 'signed int')

    def int_literal(node):
        if isinstance(node, a.UnaryOp) and node.op in ('-', '+'):
            node = node.expr
        if not isinstance(node, a.Constant) or node.type != 'int':
            return False
        if not re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)', node.value):
            return False
        # Stay in the range guaranteed for C's signed int. Do not infer a
        # game's word width from its alias spelling or the host Python ABI.
        return int(node.value, 16 if node.value.lower().startswith('0x') else 10) <= 0x7fff

    declarations = {}
    for n in nodes:
        if isinstance(n, a.Decl) and n.name:
            declarations.setdefault(n.name, []).append(n)
    escaped = {n.expr.name for n in nodes if isinstance(n, a.UnaryOp)
               and n.op == '&' and isinstance(n.expr, a.ID)}
    locals_ = {n.name: n for n in (fn.body.block_items or []) if isinstance(n, a.Decl)}
    parameters = {d.name for d in (fn.decl.type.args.params if fn.decl.type.args else [])
                  if isinstance(d, a.Decl)}
    direct = {}
    for n in tree.ext:
        if isinstance(n, a.Decl) and isinstance(n.type, a.FuncDecl):
            direct.setdefault(n.name, []).append(n)
    reserved = {n.name for n in nodes if isinstance(n, (a.ID, a.Decl, a.Typedef))}
    changed = copy.deepcopy(tree)
    target = next(n for n in changed.ext if isinstance(n, a.FuncDef))
    new_declarations = []
    mutations = 0
    for *_, block in list(rules._nodes(target.body)):
        if not isinstance(block, a.Compound):
            continue
        statements = block.block_items or []
        for i in reversed(range(len(statements) - 1)):
            default, branch = statements[i:i+2]
            if not (isinstance(default, a.Assignment) and default.op == '='
                    and isinstance(default.lvalue, a.ID) and int_literal(default.rvalue)
                    and isinstance(branch, a.If) and isinstance(branch.cond, a.BinaryOp)
                    and branch.cond.op in ('<', '<=', '>', '>=', '==', '!=')):
                continue
            name = default.lvalue.name
            declaration = locals_.get(name)
            if (declaration is None or name in escaped or name in parameters
                    or len(declarations.get(name, [])) != 1 or declaration.storage
                    or declaration.quals or declaration.funcspec
                    or not signed_int(declaration.type)):
                continue
            calls = [n for *_, n in rules._nodes(branch.cond) if isinstance(n, a.FuncCall)]
            if len(calls) != 1:
                continue
            call = calls[0]
            if not (isinstance(call.name, a.ID)
                    and (branch.cond.left is call or branch.cond.right is call)):
                continue
            literal = branch.cond.right if branch.cond.left is call else branch.cond.left
            if not int_literal(literal):
                continue
            if any(isinstance(n, a.ID) and n.name == name for *_, n in rules._nodes(branch.cond)):
                continue
            prototypes = direct.get(call.name.name, [])
            # A nested block/for-scope function pointer can shadow the global
            # prototype. No name with any additional declaration is inferred.
            if (len(prototypes) != 1 or len(declarations.get(call.name.name, [])) != 1
                    or call.name.name in locals_ or call.name.name in parameters
                    or not signed_int(prototypes[0].type.type)):
                continue
            fresh = 'callback_status'
            if fresh in reserved:
                fresh = next('callback_status_' + str(j) for j in range(10000)
                             if 'callback_status_' + str(j) not in reserved)
            reserved.add(fresh)
            new = copy.deepcopy(declaration)
            new.name, new.type.declname, new.init = fresh, fresh, None
            new_declarations.append((name, new))
            if branch.cond.left is call:
                branch.cond.left = a.ID(fresh)
            else:
                branch.cond.right = a.ID(fresh)
            statements.insert(i, a.Assignment('=', a.ID(fresh), copy.deepcopy(call)))
            mutations += 1
    if mutations:
        # Uninitialized scalar declarations add no evaluation, and remain C89.
        for name, new in new_declarations:
            i = next(i for i, n in enumerate(target.body.block_items)
                     if isinstance(n, a.Decl) and n.name == name)
            target.body.block_items.insert(i+1, new)
        emitted = rules._emit(source, changed)
        if re.findall(r'/\*\s*cflags\b.*?\*/', emitted, re.S) != flags:
            return
        yield 'private-integer-default-after-callback', emitted
