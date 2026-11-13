"""Delay an unobserved private FP literal default until after a direct callback.

This is source-semantic preserving, not a claim that an input's ABI is correct.
No FP arithmetic is reassociated and no comparison is complemented.
"""
import copy
import re
from search_rules import Variants


def generate(source, repository):
    if re.search(r'^\s*#\s*(?:define|undef)\b', source, re.M):
        return
    rules = Variants(repository)
    tree, fn = rules.parse(source)
    a = rules.a
    nodes = list(rules._nodes(fn))
    if any(isinstance(n, a.Typedef) for *_, n in nodes):
        return
    if any(q in ('volatile', '_Atomic') for *_, n in nodes
           for q in getattr(n, 'quals', [])):
        return
    # Refuse typedef shadowing even outside the function body.
    if re.search(r'\btypedef\b', re.sub(r'/\*.*?\*/|//[^\n]*', '', source, flags=re.S)):
        return
    declarations = {}
    for *_, n in nodes:
        if isinstance(n, a.Decl) and n.name:
            declarations.setdefault(n.name, []).append(n)
    escaped = {n.expr.name for *_, n in nodes
               if isinstance(n, a.UnaryOp) and n.op == '&'
               and isinstance(n.expr, a.ID)}
    direct = {}
    for decl in tree.ext:
        if isinstance(decl, a.Decl) and isinstance(decl.type, a.FuncDecl):
            direct.setdefault(decl.name, []).append(decl)
    reserved = {n.name for *_, n in rules._nodes(tree)
                if isinstance(n, (a.ID, a.Decl, a.Typedef))}
    other = copy.deepcopy(tree)
    target = next(n for n in other.ext if isinstance(n, a.FuncDef))
    changed = False
    new_declarations = []
    for *_, block in list(rules._nodes(target.body)):
        if not isinstance(block, a.Compound):
            continue
        statements = block.block_items or []
        for i in reversed(range(len(statements) - 1)):
            assignment, branch = statements[i:i + 2]
            # An entry label, when present, must continue to precede the entire
            # sequence. It may not label just the callback or the if condition.
            label = assignment if isinstance(assignment, a.Label) else None
            if label is not None:
                assignment = label.stmt
            if not (isinstance(assignment, a.Assignment) and assignment.op == '='
                    and isinstance(assignment.lvalue, a.ID)
                    and isinstance(assignment.rvalue, a.Constant)
                    and assignment.rvalue.type == 'float'
                    and isinstance(branch, a.If)
                    and isinstance(branch.cond, a.BinaryOp)
                    and branch.cond.op in ('<', '<=', '>', '>=', '==', '!=')):
                continue
            name = assignment.lvalue.name
            ds = declarations.get(name, [])
            if len(ds) != 1 or name in escaped:
                continue
            decl = ds[0]
            if not any(item is decl for item in (fn.body.block_items or [])):
                # A declaration in a sibling/nested scope must not make a
                # same-named file-scope assignment appear private.
                continue
            if (decl.storage or decl.quals or not isinstance(decl.type, a.TypeDecl)
                    or rules.generator.visit(decl.type) not in ('f32', 'float')):
                continue
            # Parameters are caller values, not private default locals.
            if fn.decl.type.args and any(d is decl for d in fn.decl.type.args.params):
                continue
            calls = [n for *_, n in rules._nodes(branch.cond)
                     if isinstance(n, a.FuncCall)]
            if len(calls) != 1:
                continue
            call = calls[0]
            if not (isinstance(call.name, a.ID)
                    and (branch.cond.left is call or branch.cond.right is call)):
                continue
            if declarations.get(call.name.name):
                # A parameter/local function pointer can shadow an otherwise
                # explicit file-scope function declaration with another ABI.
                continue
            literal = branch.cond.right if branch.cond.left is call else branch.cond.left
            if not isinstance(literal, a.Constant) or literal.type != 'float':
                continue
            if any(isinstance(n, a.ID) and n.name == name
                   for *_, n in rules._nodes(branch.cond)):
                continue
            prototypes = direct.get(call.name.name, [])
            if len(prototypes) != 1:
                continue
            prototype = prototypes[0]
            if rules.generator.visit(prototype.type.type) not in ('f32', 'float'):
                continue
            fresh = next('rac4_callback_result_' + str(j) for j in range(10000)
                         if 'rac4_callback_result_' + str(j) not in reserved)
            reserved.add(fresh)
            new_decl = copy.deepcopy(decl)
            new_decl.name = fresh
            new_decl.type.declname = fresh
            new_decl.init = None
            new_declarations.append(new_decl)
            call_assignment = a.Assignment('=', a.ID(fresh), copy.deepcopy(call))
            if branch.cond.left is call:
                branch.cond.left = a.ID(fresh)
            else:
                branch.cond.right = a.ID(fresh)
            if label is None:
                statements.insert(i, call_assignment)
            else:
                label.stmt = call_assignment
                statements.insert(i + 1, assignment)
            changed = True
    if changed:
        target.body.block_items[0:0] = new_declarations
        yield 'private-fp-konstante-nach-callback', rules._emit(source, other)
