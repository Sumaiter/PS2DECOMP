"""Conservative sequential scalar-copy temporary search variant.

Only direct, unqualified primitive pointer parameters and constant byte offsets
qualify as addresses. Every load still precedes its own store, including overlap.
The source function, ABI, native widths, values and compiler flags are unchanged.
"""
import copy
import re


LABEL = 'sequentielle-wortkopie-mit-skalarem-temporaerwert'


def children(source, variants):
    """Return at most one search child using the caller's existing Variants API.

    Compiler, reference, claim and search-budget checks remain with the caller.
    This source transformation neither certifies a match nor changes flags.
    """
    generated = generate(variants, source)
    return [(LABEL, generated)] if generated is not None else []


def generate(rules, source):
    a = rules.a
    try:
        # A caller may cache parser results: never mutate its input AST.
        tree, fn = copy.deepcopy(rules.parse(source))
    except Exception:
        return None
    nodes = [n for *_, n in rules._nodes(tree)]
    body_nodes = [n for *_, n in rules._nodes(fn.body)]
    # Reject hidden qualifiers/types and compiler constructs rather than infer
    # their effects. common.h's trusted typedef prelude is absent from this AST.
    for n in nodes:
        if isinstance(n, a.Typedef):
            return None
        if any(getattr(n, key, None) for key in
               ('quals', 'align', 'gcc_attributes', 'asmlabel')):
            return None
        if 'register' in getattr(n, 'storage', []):
            return None
        name = getattr(n, 'name', None)
        if isinstance(name, str) and (name.startswith('__') or name in {'asm', '_asm'}):
            return None
        if 'asm' in type(n).__name__.lower():
            return None
    if any(isinstance(n, a.FuncCall) for n in body_nodes):
        return None

    def pointer_type(node):
        if not isinstance(node, a.PtrDecl) or node.quals:
            return None
        ty = node.type
        if not isinstance(ty, a.TypeDecl) or ty.quals or not isinstance(ty.type, a.IdentifierType):
            return None
        return ty.type.names[0] if ty.type.names in (
            ['void'], ['s8'], ['u8'], ['s32'], ['u32']) else None

    args = fn.decl.type.args
    params = list(args.params) if args else []
    if any(isinstance(p, a.Decl) and isinstance(p.type, a.PtrDecl) and not pointer_type(p.type) for p in params):
        return None
    pointers = {p.name: pointer_type(p.type) for p in params if isinstance(p, a.Decl)}
    pointers = {name: ty for name, ty in pointers.items() if name and ty}
    if not pointers:
        return None
    declarations = [n for n in nodes if isinstance(n, a.Decl) and n.name]
    if any(sum(n.name == name for n in declarations) != 1 for name in pointers):
        return None  # Any local/global shadow makes name binding uncertain.
    bound = {n.name for n in declarations}
    bound.update(n.name for n in nodes if isinstance(n, a.Enumerator))
    if any(isinstance(n, a.ID) and n.name not in bound | {'NULL'} for n in body_nodes):
        return None

    def contains_pointer(node):
        return any(isinstance(n, a.ID) and n.name in pointers
                   for *_, n in rules._nodes(node))

    def memory_read(node):
        return any(isinstance(n, (a.ArrayRef, a.StructRef)) or
                   isinstance(n, a.UnaryOp) and n.op == '*'
                   for *_, n in rules._nodes(node))

    # Reject reassignment, address escape, pointer-valued stores/returns and
    # unknown pointer escape paths throughout the function, not just in a run.
    for n in body_nodes:
        if isinstance(n, a.Assignment):
            if isinstance(n.lvalue, a.ID) and n.lvalue.name in pointers:
                return None
            if contains_pointer(n.rvalue) and not memory_read(n.rvalue):
                return None
        if isinstance(n, a.Decl) and n.init is not None:
            if contains_pointer(n.init) and not memory_read(n.init):
                return None
        if isinstance(n, a.Return) and n.expr is not None:
            if contains_pointer(n.expr) and not memory_read(n.expr):
                return None
        if isinstance(n, a.UnaryOp) and n.op in ('&', '++', '--', 'p++', 'p--'):
            if contains_pointer(n.expr):
                return None

    def integer_constant(node):
        if isinstance(node, a.UnaryOp) and node.op in ('+', '-'):
            value = integer_constant(node.expr)
            return (-value if node.op == '-' else value) if value is not None else None
        if not isinstance(node, a.Constant) or node.type not in ('int', 'unsigned int'):
            return None
        spelling = re.sub(r'[uUlL]+$', '', node.value)
        try:
            value = int(spelling, 16 if spelling.lower().startswith('0x') else
                        8 if len(spelling) > 1 and spelling.startswith('0') else 10)
        except ValueError:
            return None
        return value if 0 <= value <= 0x7fffffff else None

    def address(node):
        if isinstance(node, a.ID):
            return pointers.get(node.name)
        if isinstance(node, a.Cast):
            ty = pointer_type(node.to_type.type)
            return ty if ty and address(node.expr) else None
        if isinstance(node, a.BinaryOp) and node.op in ('+', '-'):
            # Arithmetic must explicitly use byte pointers. No indexed loads,
            # global addresses, integer pointer conversions or unknown indices.
            if address(node.left) in ('s8', 'u8') and integer_constant(node.right) is not None:
                return address(node.left)
            if node.op == '+' and integer_constant(node.left) is not None and address(node.right) in ('s8', 'u8'):
                return address(node.right)
        return None

    # Every pointer-parameter occurrence must itself be a whitelisted memory
    # address or a null/truth guard. This also rejects compound escape forms
    # such as destination = parameter + *index that a side-effect scan misses.
    parents = {id(n): parent for parent, _, _, n in rules._nodes(fn.body)}
    for n in body_nodes:
        if not isinstance(n, a.ID) or n.name not in pointers:
            continue
        top = n
        while True:
            parent = parents.get(id(top))
            if isinstance(parent, (a.Cast, a.BinaryOp)) and address(parent):
                top = parent
            else:
                break
        parent = parents.get(id(top))
        if isinstance(parent, a.UnaryOp) and parent.op == '*' and address(parent.expr):
            continue
        if isinstance(parent, a.UnaryOp) and parent.op == '!':
            continue
        if isinstance(parent, (a.If, a.While, a.DoWhile)) and parent.cond is top:
            continue
        if isinstance(parent, a.BinaryOp) and parent.op in ('==', '!='):
            other = parent.right if parent.left is top else parent.left
            if isinstance(other, a.ID) and other.name == 'NULL' or integer_constant(other) == 0:
                continue
        return None

    def load(node):
        outer = None
        if isinstance(node, a.Cast):
            ty = node.to_type.type
            if not isinstance(ty, a.TypeDecl) or not isinstance(ty.type, a.IdentifierType):
                return None
            outer = ty.type.names[0] if ty.type.names in (['s32'], ['u32']) else None
            if outer is None:
                return None
            node = node.expr
        if not isinstance(node, a.UnaryOp) or node.op != '*' or not isinstance(node.expr, a.Cast):
            return None
        ty = pointer_type(node.expr.to_type.type)
        if ty not in ('s32', 'u32') or outer and outer != ty:
            return None
        return ty if address(node.expr.expr) else None

    candidates = []
    for node in body_nodes:
        if not isinstance(node, a.Compound):
            continue
        run = []
        for item in (node.block_items or []) + [None]:
            ty = None
            if isinstance(item, a.Assignment) and item.op == '=':
                left, right = load(item.lvalue), load(item.rvalue)
                if left and left == right:
                    ty = left
            if ty and (not run or ty == run[0][1]):
                run.append((item, ty))
                continue
            if len(run) >= 2:
                candidates.append((node, run))
            run = [(item, ty)] if ty else []
    if not candidates:
        return None
    # Reserve identifiers in every namespace, including enum/type/tag names.
    used = set(re.findall(r'\b[A-Za-z_]\w*\b', source))
    serial, name = 0, 'rac4_copy_word'
    while name in used:
        serial += 1
        name = 'rac4_copy_word_' + str(serial)
    compound, run = max(candidates, key=lambda r: len(r[1]))
    first = next(i for i, item in enumerate(compound.block_items) if item is run[0][0])
    replacement = []
    for item, ty in run:
        replacement.extend([a.Assignment('=', a.ID(name), copy.deepcopy(item.rvalue)),
                            a.Assignment('=', copy.deepcopy(item.lvalue), a.ID(name))])
    compound.block_items[first:first + len(run)] = replacement
    _, declfn = rules.parse('void placeholder(void) { ' + run[0][1] + ' ' + name + '; }')
    fn.body.block_items.insert(0, copy.deepcopy(declfn.body.block_items[0]))
    return rules._emit(source, tree)
