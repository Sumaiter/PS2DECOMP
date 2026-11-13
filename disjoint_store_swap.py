"""One pure AST variant: commute two adjacent, independent ordinary stores.

The caller supplies its pinned common.h text and verified target address width.
No files, compiler, native bytes, claims, history or application state are read.
The rule preserves every originally defined C execution; it does not prove that
the source describes the native function. Unknown effects and types fail closed.
"""
import copy
import re

LABEL = 'benachbarte-disjunkte-stores-tauschen'
# Conservative subset of the existing public backend gate. New compiler options
# require review here; options that make ordinary accesses volatile cannot enter.
SAFE_FLAGS = {'-ffast-math', '-fno-opt-stack', '-mno-split-addresses',
              '-mcheck-zero-division', '@ee29', '@lit4', '@ee29-01', '-O2', '-G0',
              '-Wa,-G0', '-fno-schedule-insns', '-fno-schedule-insns2'}


def _code(text):
    return re.sub(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|/\*.*?\*/|//[^\n]*',
                  lambda m: m[1] if m[1] else ' ', text, flags=re.S)


def _tokens(text):
    return re.findall(r'[A-Za-z_]\w*|\d[\w.]*|\S', _code(text))


def _header(rules, header):
    # Parsing only the typedef prelude would miss token-changing header macros.
    if not isinstance(header, str):
        return False
    text = _code(header)
    directives = re.findall(r'^\s*#([^\n]*)$', text, re.M)
    expected = ['ifndef COMMON_H', 'define COMMON_H',
                'define NULL ((void *)0)', 'endif']
    if [_tokens(line) for line in directives] != [_tokens(line) for line in expected]:
        return False
    body = re.sub(r'^\s*#.*$', '', text, flags=re.M)
    return _tokens(body) == _tokens(rules.prelude)


def generate(source, rules, *, address_bits=None, common_header=None):
    """Return C or None. address_bits must come from the verified EE ABI gate."""
    if type(address_bits) is not int or address_bits != 32 or not _header(rules, common_header):
        return None
    text = _code(source)
    directives = re.findall(r'^\s*#.*$', text, re.M)
    if len(directives) != 1 or any(not re.fullmatch(r'\s*#\s*include\s*"common.h"\s*', d) for d in directives):
        return None
    if re.search(r'\b(?:__\w*|asm|_asm|typeof|_Atomic|_Alignas|_Thread_local)\b', text):
        return None
    comments = re.findall(r'/\*.*?\*/|//[^\n]*', source, re.S)
    flag_comments = [c for c in comments if re.search(r'\bcflags\s*:', c)]
    if len(flag_comments) > 1 or any(not re.fullmatch(r'/\* cflags: [^\n]* \*/', c)
                                      or c not in source.splitlines()[:3] for c in flag_comments):
        return None
    if flag_comments and any(flag not in SAFE_FLAGS for flag in flag_comments[0][11:-3].split()):
        return None
    a = rules.a
    try:
        tree, fn = copy.deepcopy(rules.parse(source))
        prelude = rules.parser().parse(rules.prelude)
    except Exception:
        return None
    nodes = [n for *_, n in rules._nodes(tree)]
    # Qualifications/extensions can be hidden in nested pointer/typedef nodes.
    if any(isinstance(n, a.Typedef) or any(getattr(n, k, None) for k in
           ('quals', 'align', 'gcc_attributes', 'asmlabel')) for n in nodes):
        return None
    aliases = {}
    for n in prelude.ext:
        if not isinstance(n, a.Typedef) or n.name in aliases:
            return None
        aliases[n.name] = n.type

    primitives = {'void': ('void', 0), 'signed char': ('signed', 1),
                  'unsigned char': ('unsigned', 1), 'char': ('char', 1),
                  'short': ('signed', 2), 'short int': ('signed', 2),
                  'signed short': ('signed', 2), 'signed short int': ('signed', 2),
                  'unsigned short': ('unsigned', 2), 'unsigned short int': ('unsigned', 2),
                  'int': ('signed', 4), 'signed': ('signed', 4), 'signed int': ('signed', 4),
                  'unsigned': ('unsigned', 4), 'unsigned int': ('unsigned', 4)}

    def typ(t, seen=frozenset()):
        if any(getattr(t, k, None) for k in ('quals', 'align', 'gcc_attributes', 'asmlabel')):
            return None
        if isinstance(t, a.PtrDecl):
            inner = typ(t.type, seen)
            # Ordinary object pointers only: no pointer-to-pointer/array/function
            # or aggregate/FP/qualified pointee types in this bounded rule.
            return ('pointer', 4, inner) if inner and inner[0] != 'pointer' else None
        if not isinstance(t, a.TypeDecl) or not isinstance(t.type, a.IdentifierType):
            return None
        names = t.type.names
        if len(names) == 1 and names[0] in aliases:
            return None if names[0] in seen else typ(aliases[names[0]], seen | {names[0]})
        return primitives.get(' '.join(names))

    expected = {'s8': ('signed', 1), 'u8': ('unsigned', 1),
                's16': ('signed', 2), 'u16': ('unsigned', 2),
                's32': ('signed', 4), 'u32': ('unsigned', 4)}
    if any(name not in aliases or typ(aliases[name], {name}) != target for name, target in expected.items()):
        return None
    # Whole-source unique ordinary names avoid every global/local/parameter
    # shadowing case. Forward function prototypes are not automatic values.
    declarations = [n for n in nodes if isinstance(n, a.Decl) and n.name]
    names = [n.name for n in declarations]
    if len(set(names)) != len(names) or 'NULL' in names or fn.decl.name in aliases:
        return None
    if any(isinstance(n, (a.Struct, a.Union, a.Enum, a.ArrayDecl)) for n in nodes):
        return None
    known = set(names) | {'NULL'}
    if any(isinstance(n, a.ID) and n.name not in known for n in nodes):
        return None
    function_nodes = [n for *_, n in rules._nodes(fn)]
    functions = {n.name for n in declarations if isinstance(n.type, a.FuncDecl)}
    automatic = {}
    for n in function_nodes:
        if isinstance(n, a.Decl) and n.name and n.name not in functions:
            if n.storage or n.funcspec or n.bitsize is not None:
                continue
            t = typ(n.type)
            if t and t[0] != 'void':
                automatic[n.name] = t
    # A private scalar cannot be changed by either pointee store. Reject every
    # address-of occurrence of that scalar, regardless of where it occurs.
    escaped = {n.expr.name for n in function_nodes if isinstance(n, a.UnaryOp)
               and n.op == '&' and isinstance(n.expr, a.ID)}
    for name in escaped:
        automatic.pop(name, None)

    def integer(n):
        if not isinstance(n, a.Constant) or n.type not in ('int', 'unsigned int'):
            return None
        m = re.fullmatch(r'(0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)([uU]?)', n.value)
        if not m:
            return None
        digits = m[1]
        value = int(digits, 16 if digits.lower().startswith('0x') else 8 if digits.startswith('0') else 10)
        return value if 0 <= value <= (0xffffffff if m[2] else 0x7fffffff) else None

    def location(n):
        if not isinstance(n, a.UnaryOp) or n.op != '*' or not isinstance(n.expr, a.Cast):
            return None
        cast = n.expr
        outer = cast.to_type.type
        if not isinstance(outer, a.PtrDecl) or outer.quals:
            return None
        stored = typ(outer.type)
        if not stored or stored[0] == 'void':
            return None
        e = cast.expr
        if not isinstance(e, a.BinaryOp) or e.op != '+' or not isinstance(e.left, a.Cast):
            return None
        base_cast = e.left
        bt = typ(base_cast.to_type.type)
        if (not bt or bt[0] != 'pointer' or bt[2] not in
                {('signed', 1), ('unsigned', 1), ('char', 1)}
                or not isinstance(base_cast.expr, a.ID)):
            return None
        base = base_cast.expr.name
        if base not in automatic or automatic[base][0] != 'pointer':
            return None
        off = integer(e.right)
        if off is None or off + stored[1] > 0x7fffffff:
            return None
        return base, off, stored

    def value(n, target):
        if isinstance(n, a.ID):
            if n.name == 'NULL':
                return target[0] == 'pointer'
            t = automatic.get(n.name)
            if not t:
                return False
            if target[0] == 'pointer':
                return (t[0] == 'pointer' and (t[2] == target[2]
                        or t[2][0] == 'void' or target[2][0] == 'void'))
            return t[0] in ('signed', 'unsigned', 'char')
        constant = integer(n)
        return constant is not None and (target[0] != 'pointer' or constant == 0)

    for n in function_nodes:
        if not isinstance(n, a.Compound):
            continue
        items = n.block_items or []
        for i, (left, right) in enumerate(zip(items, items[1:])):
            if not all(isinstance(s, a.Assignment) and s.op == '=' for s in (left, right)):
                continue
            x, y = location(left.lvalue), location(right.lvalue)
            if not x or not y or x[0] != y[0]:
                continue
            if not (x[1] + x[2][1] <= y[1] or y[1] + y[2][1] <= x[1]):
                continue
            if not value(left.rvalue, x[2]) or not value(right.rvalue, y[2]):
                continue
            # Only the two statement positions change. No expression is edited.
            items[i], items[i + 1] = right, left
            return rules._emit(source, tree)
    return None


def children(source, variants, *, address_bits=None, common_header=None):
    output = generate(source, variants, address_bits=address_bits, common_header=common_header)
    return [(LABEL, output)] if output is not None else []
