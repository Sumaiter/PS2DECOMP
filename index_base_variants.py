"""Materialize an existing cast pointer before an unconditional comparison.

The cast type and address expression come from the input. No element width,
array extent, ABI, address or native instruction pattern is inferred.
"""
import copy
import re

from search_rules import Variants


def generate(source, repository):
    if re.search(r'\b(?:__LINE__|__FILE__|__COUNTER__|__attribute__|__declspec|_Alignas|__asm__|asm)\b', source):
        return
    rules = Variants(repository)
    if (not rules.plain_header()
            or re.search(r'\b(?:__attribute__|__declspec|_Alignas)\b', rules.header_text)):
        return
    try:
        tree, fn = rules.parse(source)
    except Exception:
        return
    a = rules.a
    prefix = rules.parser().parse(rules.prelude)
    all_nodes = [n for *_, n in rules._nodes(tree)]
    if any(getattr(n, 'quals', None) for n in all_nodes +
           [n for *_, n in rules._nodes(prefix)]):
        return
    if any(isinstance(n, (a.Goto, a.Label)) for n in all_nodes):
        return
    typedefs = {n.name: n.type for n in prefix.ext if isinstance(n, a.Typedef)}
    for n in tree.ext:
        if isinstance(n, a.Typedef):
            if n.name in typedefs:
                return
            typedefs[n.name] = n.type

    integral = {'char', 'signed char', 'unsigned char', 'short', 'short int',
                'signed short', 'signed short int', 'unsigned short',
                'unsigned short int', 'int', 'signed', 'signed int', 'unsigned',
                'unsigned int', 'long', 'long int', 'signed long',
                'signed long int', 'unsigned long', 'unsigned long int',
                'long long', 'long long int', 'unsigned long long',
                'unsigned long long int'}

    def kind(node, seen=frozenset()):
        if getattr(node, 'quals', None):
            return None
        if isinstance(node, a.PtrDecl):
            inner = kind(node.type, seen)
            return ('pointer', inner) if inner else None
        if isinstance(node, a.TypeDecl):
            return kind(node.type, seen)
        if isinstance(node, a.IdentifierType):
            spelling = ' '.join(node.names)
            if spelling in integral:
                return 'byte' if spelling in ('char', 'signed char', 'unsigned char') else 'integer'
            if spelling in ('float', 'double', 'long double'):
                return 'float'
            if spelling in typedefs and spelling not in seen:
                return kind(typedefs[spelling], seen | {spelling})
        return None

    declarations = {}
    locals_ = []
    if fn.decl.type.args:
        locals_ += [p for p in fn.decl.type.args.params if isinstance(p, a.Decl)]
    locals_ += [n for *_, n in rules._nodes(fn.body) if isinstance(n, a.Decl)]
    for declaration in locals_ + [n for n in tree.ext if isinstance(n, a.Decl)
                                 and not isinstance(n.type, a.FuncDecl)]:
        if declaration.name:
            declarations.setdefault(declaration.name, []).append(declaration)
    escaped = {n.expr.name for n in all_nodes if isinstance(n, a.UnaryOp)
               and n.op == '&' and isinstance(n.expr, a.ID)}

    def declaration(name):
        found = declarations.get(name, [])
        return found[0] if len(found) == 1 else None

    def pure(expr):
        if isinstance(expr, a.Constant):
            return expr.type in ('int', 'unsigned int', 'float', 'double')
        if isinstance(expr, a.ID):
            d = declaration(expr.name)
            return bool(d and kind(d.type))
        if isinstance(expr, a.Cast):
            return bool(kind(expr.to_type.type)) and pure(expr.expr)
        if isinstance(expr, a.UnaryOp) and expr.op in ('*', '+', '-', '~', '!'):
            return pure(expr.expr)
        if isinstance(expr, a.BinaryOp) and expr.op not in ('&&', '||'):
            return pure(expr.left) and pure(expr.right)
        if isinstance(expr, a.ArrayRef):
            return pure(expr.name) and pure(expr.subscript)
        return False

    nodes = list(rules._nodes(fn.body))
    reserved = {n.name for n in all_nodes if isinstance(n, (a.ID, a.Decl, a.Typedef))}
    reserved |= set(typedefs)
    name = next('ps2decomp_index_base_' + str(i) for i in range(10000)
                if 'ps2decomp_index_base_' + str(i) not in reserved)
    for position, (_, _, _, block) in enumerate(nodes):
        if not isinstance(block, a.Compound):
            continue
        for index, statement in enumerate(block.block_items or []):
            if not isinstance(statement, a.If):
                continue
            cond = statement.cond
            if not isinstance(cond, a.BinaryOp) or cond.op not in ('==', '!=', '<', '<=', '>', '>=') or not pure(cond):
                continue
            arrays = [n for *_, n in rules._nodes(cond) if isinstance(n, a.ArrayRef)]
            if len(arrays) != 1:
                continue
            array = arrays[0]
            cast = array.name
            if not isinstance(cast, a.Cast) or not isinstance(cast.to_type.type, a.PtrDecl):
                continue
            if kind(cast.to_type.type.type) not in ('byte', 'integer', 'float'):
                continue
            address = cast.expr
            if (not isinstance(address, a.BinaryOp) or address.op != '+'
                    or not isinstance(address.left, a.ID)
                    or not isinstance(address.right, a.Constant)
                    or address.right.type != 'int'
                    or not isinstance(array.subscript, a.ID)):
                continue
            base, subscript = declaration(address.left.name), declaration(array.subscript.name)
            if (base is None or base not in locals_ or base.storage
                    or kind(base.type) != ('pointer', 'byte')
                    or address.left.name in escaped or array.subscript.name in escaped
                    or subscript is None or kind(subscript.type) not in ('byte', 'integer')):
                continue
            other = copy.deepcopy(tree)
            target = next(n for n in other.ext if isinstance(n, a.FuncDef))
            target_block = list(rules._nodes(target.body))[position][3]
            target_if = target_block.block_items[index]
            target_array = next(n for *_, n in rules._nodes(target_if.cond) if isinstance(n, a.ArrayRef))
            assignment = a.Assignment('=', a.ID(name), target_array.name)
            target_array.name = a.ID(name)
            decl = copy.deepcopy(base)
            decl.name, decl.init, decl.storage = name, None, []
            decl.type = copy.deepcopy(cast.to_type.type)
            inner = decl.type
            while not isinstance(inner, a.TypeDecl):
                inner = inner.type
            inner.declname = name
            target_block.block_items.insert(index, assignment)
            declarations_end = 0
            while (declarations_end < len(target.body.block_items)
                   and isinstance(target.body.block_items[declarations_end], (a.Decl, a.Typedef))):
                declarations_end += 1
            target.body.block_items.insert(declarations_end, decl)
            yield 'materialize-index-base', rules._emit(source, other)
