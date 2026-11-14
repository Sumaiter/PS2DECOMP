"""Read-only plain-C diagnostics; neither address repair nor semantic approval."""
import re
import integer_pointer_diagnostics
from search_rules import Variants, code_only


def inspect(source, repository):
    code = code_only(source)
    violations = []
    # Discard string and character contents only for keyword diagnostics.
    tokens = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', ' ', code)
    for word in sorted(set(re.findall(r'\b(?:volatile|__asm__|asm|__attribute__)\b', tokens))):
        violations.append(dict(kind='unsupported-keyword', expression=word))
    directives = re.findall(r'^\s*#\s*define\b[^\n]*', code, re.M)
    for line in directives:
        violations.append(dict(kind='macro-needs-manual-review', expression=line.strip()))
    parsed = False
    reason = None
    conversions = []
    conversion_reason = None
    try:
        rules = Variants(repository)
        tree = rules.parse_unit(source)
        a = rules.a
        scopes = [{}]

        def pointer(node):
            if isinstance(node, a.PtrDecl):
                return True
            if isinstance(node, a.TypeDecl) and isinstance(node.type, a.IdentifierType):
                names = node.type.names
                if len(names) == 1:
                    for scope in reversed(scopes):
                        if names[0] in scope:
                            return scope[names[0]]
            return False

        def integer_literal(node):
            if isinstance(node, a.UnaryOp) and node.op in ('+', '-'):
                value = integer_literal(node.expr)
                return None if value is None else value * (-1 if node.op == '-' else 1)
            if not isinstance(node, a.Constant) or node.type not in ('int', 'unsigned int', 'long', 'long int', 'unsigned long', 'unsigned long int', 'long long', 'long long int', 'unsigned long long', 'unsigned long long int'):
                return None
            spelling = re.sub(r'[uUlL]+$', '', node.value)
            try:
                # C89 octal spelling is different from Python's base-zero form.
                return int(spelling, 8 if re.fullmatch(r'0[0-7]+', spelling) else 0)
            except ValueError:
                return None

        def inspect_node(node):
            if isinstance(node, (a.Compound, a.For, a.FuncDef)):
                scopes.append({})
                for _, child in node.children():
                    inspect_node(child)
                scopes.pop()
                return
            if isinstance(node, a.Typedef):
                # Capture the alias now, before a later scope shadows its source.
                binding = pointer(node.type)
                for _, child in node.children():
                    inspect_node(child)
                scopes[-1][node.name] = binding
                return
            if isinstance(node, a.Cast) and pointer(node.to_type.type):
                value = integer_literal(node.expr)
                if value is not None and value != 0:
                    violations.append(dict(kind='literal-pointer-address', expression=rules.generator.visit(node), value=value))
            for _, child in node.children():
                inspect_node(child)

        for node in rules.parser().parse(rules.prelude).ext:
            inspect_node(node)
        inspect_node(tree)
        parsed = True
        try:
            conversions = integer_pointer_diagnostics.inspect(rules, tree)
        except Exception as exc:
            conversion_reason = str(exc)[:500]
    except Exception as exc:
        reason = str(exc)[:500]
    return dict(astAnalyzed=parsed, violations=violations, analysisLimitation=reason,
                integerPointerConversions=conversions,
                integerPointerAnalysisLimitation=conversion_reason if parsed else reason,
                integerPointerScope='Known local/parameter pointer assignments and initializers from lexical integer expressions only; block-scope extern destinations are excluded. No flow, call, field, target-width or ownership inference; unknown and potentially null constant expressions are skipped. Diagnostic only; does not change violations or eligibility.',
                semanticApproval=False,
                scope='Direct nonzero integer-to-pointer literals and explicit unsupported source constructs only. Absence of findings is not approval.')
