"""Narrow type guard for the existing terminal-return join.

No source/address/native mapping is inferred. A direct explicit pointer cast
already declares the loaded primitive type. The caller owns its existing
canonical scalar resolver, including hidden typedef qualifiers.
"""


def explicit_primitive_dereference_type(expression, ast, canonical_scalar, *, source_typedef_names=frozenset()):
    """Return the existing primitive spelling, or None for manual cases.

    This does not transform expressions or move/evaluate an address. It is
    intended only as an additional terminal_expression_type case; the existing
    terminal_returns shape/type/flow guards and join implementation stay in use.
    """
    if not isinstance(expression, ast.UnaryOp) or expression.op != "*":
        return None
    cast = expression.expr
    if not isinstance(cast, ast.Cast):
        return None
    pointer = cast.to_type.type
    if not isinstance(pointer, ast.PtrDecl):
        return None

    def walk(node):
        yield node
        for _, child in node.children():
            yield from walk(child)

    # Keep qualified/extension-bearing casts and address expressions manual.
    # Calls are deliberately retained: the existing join evaluates only the
    # selected arm exactly once, and never hoists the call above its guard.
    for node in walk(cast):
        if isinstance(node, ast.IdentifierType) and set(node.names) & set(source_typedef_names):
            return None
        if any(getattr(node, key, None) for key in ("quals", "align", "gcc_attributes", "asmlabel", "funcspec")):
            return None
        if isinstance(node, ast.ID) and node.name.startswith('__'):
            return None
        if isinstance(node, (ast.Assignment, ast.ExprList)):
            return None
        if isinstance(node, ast.UnaryOp) and node.op in ("p++", "p--", "++", "--"):
            return None
    return canonical_scalar(pointer.type)
