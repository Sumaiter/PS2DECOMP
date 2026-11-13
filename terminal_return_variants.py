"""Preserve typed terminal branch results without a live result temporary.

This rewrites only ``result = value; } return result;`` at the terminal outer
if arm.  Initializers, tests, side effects, calls, and the default result path
are deliberately retained.  A search variant is never a semantic approval or
a reason to bypass compiler/all-placement relocation checks.
"""
import copy
from search_rules import Variants


def generate(source, repository):
    rules = Variants(repository)
    try:
        tree, fn = rules.parse(source)
    except Exception:
        return
    a = rules.a
    nodes = [node for *_, node in rules._nodes(tree)]
    if any(isinstance(node, (a.Typedef, a.Goto, a.Label, a.For, a.While,
                              a.DoWhile, a.Switch)) for node in nodes):
        return
    statements = fn.body.block_items or []
    if (len(statements) < 2 or not isinstance(statements[-1], a.Return)
            or not isinstance(statements[-1].expr, a.ID)
            or not isinstance(statements[-2], a.If)):
        return
    result_name = statements[-1].expr.name
    declarations = {}
    for node in nodes:
        if isinstance(node, a.Decl) and node.name:
            declarations.setdefault(node.name, []).append(node)
    locals_ = {node.name for node in statements if isinstance(node, a.Decl)}
    escaped = {node.expr.name for node in nodes
               if isinstance(node, a.UnaryOp) and node.op == '&'
               and isinstance(node.expr, a.ID)}
    aliases = {node.name: node.type for node in rules.parser().parse(rules.prelude).ext
               if isinstance(node, a.Typedef)}
    canonical = {
        'char': 'char', 'signed char': 'signed char', 'unsigned char': 'unsigned char',
        'short': 'short', 'short int': 'short', 'signed short': 'short',
        'signed short int': 'short', 'unsigned short': 'unsigned short',
        'unsigned short int': 'unsigned short', 'int': 'int', 'signed': 'int',
        'signed int': 'int', 'unsigned': 'unsigned int', 'unsigned int': 'unsigned int',
        'long': 'long', 'long int': 'long', 'signed long': 'long',
        'signed long int': 'long', 'unsigned long': 'unsigned long',
        'unsigned long int': 'unsigned long', 'long long': 'long long',
        'long long int': 'long long', 'unsigned long long': 'unsigned long long',
        'unsigned long long int': 'unsigned long long',
    }

    def integer_type(node, seen=frozenset()):
        if (not isinstance(node, a.TypeDecl) or node.quals
                or not isinstance(node.type, a.IdentifierType)):
            return None
        names = node.type.names
        if len(names) == 1 and names[0] in aliases and names[0] not in seen:
            return integer_type(aliases[names[0]], seen | {names[0]})
        return canonical.get(' '.join(names))

    def local_type(name):
        declarations_ = declarations.get(name, [])
        if name not in locals_ or name in escaped or len(declarations_) != 1:
            return None
        declaration = declarations_[0]
        if declaration.storage or declaration.funcspec or declaration.quals:
            return None
        return integer_type(declaration.type)

    if not isinstance(fn.decl.type, a.FuncDecl):
        return
    return_type = integer_type(fn.decl.type.type)
    result_type = local_type(result_name)
    if return_type is None or result_type != return_type:
        return

    def value_type(value):
        if isinstance(value, a.ID):
            return local_type(value.name)
        if isinstance(value, a.Constant):
            return canonical.get(value.type)
        # A signed constant's existing unary minus is retained exactly.  No
        # cast, expression algebra, memory access or value is synthesized.
        if (isinstance(value, a.UnaryOp) and value.op == '-'
                and isinstance(value.expr, a.Constant)):
            return canonical.get(value.expr.type)
        return None

    guard = statements[-2]
    for arm in ('iftrue', 'iffalse'):
        body = getattr(guard, arm)
        if body is None:
            continue
        tail = body.block_items[-1] if isinstance(body, a.Compound) and body.block_items else body
        if (not isinstance(tail, a.Assignment) or tail.op != '='
                or not isinstance(tail.lvalue, a.ID)
                or tail.lvalue.name != result_name
                or value_type(tail.rvalue) != result_type):
            continue
        other = copy.deepcopy(tree)
        target = next(node for node in other.ext if isinstance(node, a.FuncDef))
        branch = getattr(target.body.block_items[-2], arm)
        old = branch.block_items[-1] if isinstance(branch, a.Compound) else branch
        replacement = a.Return(old.rvalue)
        if isinstance(branch, a.Compound):
            branch.block_items[-1] = replacement
        else:
            setattr(target.body.block_items[-2], arm, replacement)
        yield 'terminal-arm-return', rules._emit(source, other)
