"""Keep a comparison's existing C int value before a scalar assignment.

This adds a search child, not a type correction or a match certificate. The
destination type, function ABI, operands, evaluation gate and flags stay intact.
"""
import copy
import json
import re
import shlex

from search_rules import Variants


def generate(source, repository):
    if re.search(r'\b(?:__[A-Za-z_]\w*|asm|_asm|_Alignas)\b', source):
        return
    # An otherwise plain common.h is not a preprocessing contract when a
    # command-line macro can redefine the newly introduced int keyword/name,
    # an include option can supply another header, or a response/specs file
    # hides such options. Never change or strip the original flags instead.
    option_sets = []
    for comment in re.findall(r'/\*\s*cflags:(.*?)\*/', source, re.S):
        try:
            option_sets.append(shlex.split(comment))
        except ValueError:
            return
    # Generic projects can configure the same active options without any
    # source comment. Inspect the visible argv/profile, not only the draft.
    manifest = repository / 'ps2decomp.json'
    if manifest.exists():
        try:
            settings = json.loads(manifest.read_text(encoding='utf-8-sig'))
            if not isinstance(settings, dict):
                return
            for key in ('sourceFlags', 'compileCommand'):
                flags = settings.get(key, [])
                if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
                    return
                option_sets.append(flags)
        except (OSError, ValueError):
            return
    unsafe = ('-D', '-U', '-I', '-F', '-A', '-i', '-Wp,', '-Xpreprocessor',
              '--include', '--define-macro', '--undefine-macro', '--sysroot',
              '--specs', '-specs', '-fplugin', '-nostdinc', '-undef', '-B')
    if any(flag.startswith(unsafe) or flag.startswith('@') or flag == '-Wp'
           for flags in option_sets for flag in flags):
        return
    rules = Variants(repository)
    if not rules.plain_header():
        return
    try:
        tree, fn = rules.parse(source)
        prefix = rules.parser().parse(rules.prelude)
    except Exception:
        return
    a = rules.a
    nodes = [n for *_, n in rules._nodes(tree)]
    header = [n for *_, n in rules._nodes(prefix)]
    if any(any(getattr(n, k, None) for k in
               ('quals', 'align', 'gcc_attributes', 'asmlabel'))
           or 'register' in getattr(n, 'storage', []) for n in nodes + header):
        return
    if any(isinstance(n, (a.Goto, a.Label, a.Typedef)) for n in nodes):
        return
    typedefs = {n.name: n.type for n in prefix.ext if isinstance(n, a.Typedef)}
    primitives = {'char', 'signed char', 'unsigned char', 'short', 'short int',
                  'signed short', 'signed short int', 'unsigned short',
                  'unsigned short int', 'int', 'signed', 'signed int',
                  'unsigned', 'unsigned int', 'long', 'long int', 'signed long',
                  'signed long int', 'unsigned long', 'unsigned long int',
                  'long long', 'long long int', 'signed long long',
                  'signed long long int', 'unsigned long long',
                  'unsigned long long int', '_Bool'}

    def scalar(ty, seen=frozenset()):
        if isinstance(ty, a.TypeDecl):
            return scalar(ty.type, seen)
        if isinstance(ty, a.IdentifierType):
            spelling = ' '.join(ty.names)
            if spelling in primitives:
                return 'integer'
            if spelling in {'float', 'double', 'long double'}:
                return 'float'
            if spelling in typedefs and spelling not in seen:
                return scalar(typedefs[spelling], seen | {spelling})
        if isinstance(ty, a.PtrDecl):
            return 'pointer'
        return None

    declarations = {}
    for n in nodes:
        if isinstance(n, a.Decl) and n.name:
            declarations.setdefault(n.name, []).append(n)
    locals_ = {id(n) for *_, n in rules._nodes(fn.body) if isinstance(n, a.Decl)}
    escaped = {n.expr.name for n in nodes if isinstance(n, a.UnaryOp)
               and n.op == '&' and isinstance(n.expr, a.ID)}

    def pure(expr):
        if isinstance(expr, a.Constant):
            return expr.type in {'int', 'unsigned int', 'float', 'double'}
        if isinstance(expr, a.ID):
            ds = declarations.get(expr.name, [])
            return len(ds) == 1 and scalar(ds[0].type) is not None
        if isinstance(expr, a.Cast):
            return scalar(expr.to_type.type) is not None and pure(expr.expr)
        if isinstance(expr, a.UnaryOp) and expr.op in {'*', '+', '-', '~', '!'}:
            return pure(expr.expr)
        if isinstance(expr, a.BinaryOp) and expr.op not in {'&&', '||'}:
            return pure(expr.left) and pure(expr.right)
        if isinstance(expr, a.ArrayRef):
            return pure(expr.name) and pure(expr.subscript)
        return False

    reserved = set(re.findall(r'\b[A-Za-z_]\w*\b', source + rules.header_text))
    name = next('ps2decomp_comparison_' + str(i) for i in range(10000)
                if 'ps2decomp_comparison_' + str(i) not in reserved)
    for position, (parent, attr, index, n) in enumerate(rules._nodes(fn.body)):
        if (not isinstance(parent, a.Compound) or attr != 'block_items'
                or not isinstance(n, a.Assignment) or n.op != '='
                or not isinstance(n.lvalue, a.ID)
                or n.lvalue.name.startswith('ps2decomp_comparison_')):
            continue
        ds = declarations.get(n.lvalue.name, [])
        if (len(ds) != 1 or id(ds[0]) not in locals_ or ds[0].storage
                or n.lvalue.name in escaped or scalar(ds[0].type) != 'integer'):
            continue
        if (not isinstance(n.rvalue, a.BinaryOp)
                or n.rvalue.op not in {'==', '!=', '<', '<=', '>', '>='}
                or not pure(n.rvalue)):
            continue
        other = copy.deepcopy(tree)
        new_fn = next(n for n in other.ext if isinstance(n, a.FuncDef))
        block, _, idx, assignment = list(rules._nodes(new_fn.body))[position]
        compare = assignment.rvalue
        assignment.rvalue = a.ID(name)
        block.block_items.insert(idx, a.Assignment('=', a.ID(name), compare))
        _, declaration_fn = rules.parse('void placeholder(void) { int ' + name + '; }')
        # No initializer is hoisted; all original expressions stay at their
        # exact statement, including when nested in a guard or loop.
        end = 0
        while (end < len(new_fn.body.block_items)
               and isinstance(new_fn.body.block_items[end], a.Decl)):
            end += 1
        new_fn.body.block_items.insert(end, declaration_fn.body.block_items[0])
        yield 'materialize-comparison-value', rules._emit(source, other)
