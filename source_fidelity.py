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
        aliases = {n.name: n.type for n in rules.parser().parse(rules.prelude).ext if isinstance(n, a.Typedef)}
        aliases.update({n.name: n.type for n in tree.ext if isinstance(n, a.Typedef)})

        def pointer(node, seen=frozenset()):
            if isinstance(node, a.PtrDecl):
                return True
            if isinstance(node, a.TypeDecl) and isinstance(node.type, a.IdentifierType):
                names = node.type.names
                if len(names) == 1 and names[0] in aliases and names[0] not in seen:
                    return pointer(aliases[names[0]], seen | {names[0]})
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

        for *_, node in rules._nodes(tree):
            if isinstance(node, a.Cast) and pointer(node.to_type.type):
                value = integer_literal(node.expr)
                if value is not None and value != 0:
                    violations.append(dict(kind='literal-pointer-address', expression=rules.generator.visit(node), value=value))
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
