"""Narrow lexical diagnostics, without flow inference or target ABI assumptions."""
import re


def inspect(rules, tree):
    a = rules.a
    scopes = [{}]
    findings = []
    unknown = ('unknown', None)

    def lookup(name):
        for scope in reversed(scopes):
            if name in scope:
                return scope[name]
        return (unknown, False, False)

    def datatype(node):
        if any(getattr(node, key, None) for key in ('gcc_attributes', 'align')):
            return unknown
        if isinstance(node, a.PtrDecl):
            return ('pointer', None)
        if isinstance(node, (a.ArrayDecl, a.FuncDecl)):
            return unknown
        if not isinstance(node, a.TypeDecl) or not isinstance(node.type, a.IdentifierType):
            return unknown
        names = node.type.names
        if len(names) == 1:
            binding, alias, _ = lookup(names[0])
            if alias:
                return binding
        words = set(names)
        if not words or not words <= {'signed', 'unsigned', 'char', 'short', 'int', 'long', '_Bool'}:
            return unknown
        # Minimum ranges guaranteed by C; these are not PS2 typedef widths.
        bits = 8 if 'char' in words else 16 if 'short' in words else 64 if names.count('long') == 2 else 32 if 'long' in words else 16
        if '_Bool' in words:
            return ('integer', (0, 1))
        if 'unsigned' in words:
            return ('integer', (0, 2 ** bits - 1))
        if 'char' in words and 'signed' not in words:
            return ('integer', (0, 127))
        return ('integer', (-(2 ** (bits - 1)) + 1, 2 ** (bits - 1) - 1))

    def declared_type(node):
        if any(getattr(node, key, None) for key in ('gcc_attributes', 'align', 'asmlabel')):
            return unknown
        return datatype(node.type)

    def expression(node):
        # kind, constant-expression flag, safely known constant value
        if isinstance(node, a.ID):
            typ, alias, _ = lookup(node.name)
            return (typ[0] if not alias else 'unknown', False, None)
        if isinstance(node, a.Constant):
            if node.type not in ('int', 'unsigned int', 'long', 'long int', 'unsigned long', 'unsigned long int', 'long long', 'long long int', 'unsigned long long', 'unsigned long long int'):
                return ('unknown', False, None)
            spelling = re.sub(r'[uUlL]+$', '', node.value)
            try:
                value = int(spelling, 8 if re.fullmatch(r'0[0-7]+', spelling) else 0)
                return ('integer', True, value)
            except ValueError:
                return ('unknown', False, None)
        if isinstance(node, a.Cast):
            typ = declared_type(node.to_type)
            kind, constant, value = expression(node.expr)
            if typ[0] != 'integer' or kind != 'integer':
                return ('unknown', False, None)
            if constant and value is not None and not typ[1][0] <= value <= typ[1][1]:
                value = None  # Narrowing, signedness and ABI-dependent conversions.
            return ('integer', constant, value)
        if isinstance(node, a.UnaryOp) and node.op in ('+', '-', '~', '!'):
            kind, constant, value = expression(node.expr)
            if kind != 'integer':
                return ('unknown', False, None)
            if node.op == '~' or (node.op == '-' and value != 0):
                # Do not model signed representations or unsigned negation. A
                # following narrowing cast could otherwise turn a guessed -1 into 0.
                return ('integer', constant, None)
            if value is not None:
                value = {'+': lambda: value, '-': lambda: -value, '~': lambda: ~value, '!': lambda: int(not value)}[node.op]()
                if not -32767 <= value <= 32767:
                    value = None
            return ('integer', constant, value)
        if isinstance(node, a.BinaryOp):
            left, lc, lv = expression(node.left)
            right, rc, rv = expression(node.right)
            if left != 'integer' or right != 'integer':
                return ('unknown', False, None)  # Includes true pointer arithmetic.
            operations = {'+': lambda: lv + rv, '-': lambda: lv - rv, '*': lambda: lv * rv,
                          '&': lambda: lv & rv, '|': lambda: lv | rv, '^': lambda: lv ^ rv,
                          '==': lambda: int(lv == rv), '!=': lambda: int(lv != rv),
                          '<': lambda: int(lv < rv), '>': lambda: int(lv > rv),
                          '<=': lambda: int(lv <= rv), '>=': lambda: int(lv >= rv),
                          '&&': lambda: int(bool(lv) and bool(rv)), '||': lambda: int(bool(lv) or bool(rv))}
            if node.op not in operations and node.op not in ('/', '%', '<<', '>>'):
                return ('unknown', False, None)
            value = None
            # Avoid signed/unsigned comparison and wraparound assumptions. Even a
            # comparison can be a null pointer constant: (0U > -1) evaluates to 0.
            if node.op in operations and lv is not None and rv is not None and 0 <= lv <= 32767 and 0 <= rv <= 32767:
                value = operations[node.op]()
                if not 0 <= value <= 32767:
                    value = None
            return ('integer', lc and rc, value)
        return ('unknown', False, None)  # Calls, fields, dereferences and complex flow.

    def check(name, rhs, context):
        typ, alias, local = lookup(name)
        if not local or alias or typ[0] != 'pointer' or rhs is None:
            return
        kind, constant, value = expression(rhs)
        # Unknown constant values can include zero after target-dependent casts.
        if kind == 'integer' and (not constant or value not in (None, 0)):
            findings.append(dict(kind='integer-to-pointer', destination=name, context=context,
                                 expression=rules.generator.visit(rhs)))

    def walk(node, local=False):
        if node is None:
            return
        if isinstance(node, a.Compound):
            scopes.append({})
            for item in node.block_items or []:
                walk(item, True)
            scopes.pop()
        elif isinstance(node, a.For):
            scopes.append({})
            for _, child in node.children():
                walk(child, True)
            scopes.pop()
        elif isinstance(node, a.FuncDef):
            scopes.append({})
            for param in node.decl.type.args.params if node.decl.type.args else []:
                if isinstance(param, a.Decl):
                    scopes[-1][param.name] = (declared_type(param), False, True)
            for item in node.body.block_items or []:
                walk(item, True)
            scopes.pop()
        elif isinstance(node, a.Typedef):
            scopes[-1][node.name] = (declared_type(node), True, local)
        elif isinstance(node, a.Decl):
            if node.name:
                scopes[-1][node.name] = (declared_type(node), False, local)
                check(node.name, node.init, 'initializer')
            walk(node.init, local)
        elif isinstance(node, a.Assignment):
            if node.op == '=' and isinstance(node.lvalue, a.ID):
                check(node.lvalue.name, node.rvalue, 'assignment')
            for _, child in node.children():
                walk(child, local)
        else:
            for _, child in node.children():
                walk(child, local)

    for node in rules.parser().parse(rules.prelude).ext:
        walk(node)
    walk(tree)
    return findings
