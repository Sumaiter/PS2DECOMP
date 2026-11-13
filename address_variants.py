"""C89-safe variants for pure byte-pointer arithmetic on the verified 32-bit EE ABI."""
import copy
import re
from search_rules import Variants, code_only, source_key


def generate(source, repository):
    if re.search(r'\b(?:volatile|asm|__asm__|rac4_address_\w+)\b', code_only(source)):
        return
    rules = Variants(repository); tree, fn = rules.parse(source); a = rules.a
    declarations = {}
    visible = [n for n in tree.ext if isinstance(n, a.Decl) and not isinstance(n.type, a.FuncDecl)]
    visible += [n for *_, n in rules._nodes(fn)]
    for n in visible:
        if isinstance(n, a.Decl) and n.name:
            declarations[n.name] = n.type if n.name not in declarations else None
    scalar = {'s32', 'u32', 'int', 'unsigned int', 'unsigned'}
    byte = {'s8', 'u8', 'char', 'signed char', 'unsigned char', 'void'}
    def spelling(t): return rules.generator.visit(t) if t is not None else ''
    def pointer(t):
        return isinstance(t, a.PtrDecl) and isinstance(t.type, a.TypeDecl) and isinstance(t.type.type, a.IdentifierType) and ' '.join(t.type.type.names) in byte
    def integer(n):
        if isinstance(n, a.ID): return spelling(declarations.get(n.name)) in scalar
        if isinstance(n, a.Constant): return n.type in ('int', 'unsigned int')
        if isinstance(n, a.UnaryOp): return n.op in ('+', '-', '~') and integer(n.expr)
        if isinstance(n, a.BinaryOp): return n.op in ('+', '-', '*', '&', '|', '^', '<<') and integer(n.left) and integer(n.right)
        if isinstance(n, a.Cast): return spelling(n.to_type.type) in scalar and integer(n.expr)
        return False
    def pointer_expr(n):
        if isinstance(n, a.ID): return pointer(declarations.get(n.name))
        return isinstance(n, a.BinaryOp) and n.op == '+' and ((pointer_expr(n.left) and integer(n.right)) or (integer(n.left) and pointer_expr(n.right)))
    def typename(typ):
        typ = copy.deepcopy(typ); leaf = typ
        while not isinstance(leaf, a.TypeDecl): leaf = leaf.type
        leaf.declname = None
        return a.Typename(name=None, quals=[], align=None, gcc_attributes=[], type=typ)
    u32 = rules.parser().parse(rules.prelude + '\nu32 rac4_address_0;').ext[-1]
    def cast32(n): return a.Cast(typename(u32.type), copy.deepcopy(n))
    def unsigned(n):
        # Convert operands before arithmetic, so signed overflow is not introduced.
        if isinstance(n, a.BinaryOp): return a.BinaryOp(n.op, unsigned(n.left), unsigned(n.right))
        if isinstance(n, a.UnaryOp): return a.UnaryOp(n.op, unsigned(n.expr))
        return cast32(n)
    def address(n):
        if isinstance(n, a.ID): return cast32(n)
        left = address(n.left) if pointer_expr(n.left) else unsigned(n.left)
        right = address(n.right) if pointer_expr(n.right) else unsigned(n.right)
        return a.BinaryOp('+', left, right)
    nodes = list(rules._nodes(fn.body)); signature = rules.generator.visit(fn.decl)
    seen = {source_key(source)}
    for index, (parent, attr, _, n) in enumerate(nodes):
        if not (isinstance(parent, a.Compound) and attr == 'block_items' and isinstance(n, a.Assignment)
                and n.op == '=' and isinstance(n.lvalue, a.ID) and pointer(declarations.get(n.lvalue.name))
                and isinstance(n.rvalue, a.BinaryOp) and n.rvalue.op == '+'):
            continue
        expr = n.rvalue
        if pointer_expr(expr.left) and integer(expr.right): base, offset = expr.left, expr.right
        elif integer(expr.left) and pointer_expr(expr.right): base, offset = expr.right, expr.left
        else: continue
        changed = copy.deepcopy(tree); f = next(x for x in changed.ext if isinstance(x, a.FuncDef))
        p, _, _, assignment = list(rules._nodes(f.body))[index]
        temporary = copy.deepcopy(u32)
        temporary.name = temporary.type.declname = 'rac4_address_0'
        # All declarations precede executable statements, as required by GCC 2.95 C89.
        position = next((i for i, x in enumerate(f.body.block_items or []) if not isinstance(x, (a.Decl, a.Typedef))), len(f.body.block_items or []))
        f.body.block_items.insert(position, temporary)
        at = p.block_items.index(assignment)
        p.block_items.insert(at, a.Assignment('=', a.ID(temporary.name), address(base)))
        typ = declarations[n.lvalue.name]
        assignment.rvalue = a.Cast(typename(typ), a.BinaryOp('+', unsigned(offset), a.ID(temporary.name)))
        assert rules.generator.visit(f.decl) == signature
        text = rules._emit(source, changed); key = source_key(text)
        if key not in seen:
            seen.add(key)
            yield '32bit-byte-address', text
