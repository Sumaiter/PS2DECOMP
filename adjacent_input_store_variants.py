"""One bounded equivalent ordering of two disjoint input-word stores.

Only identical unqualified primitive 32-bit words from unchanged, unescaped
by-value input parameters qualify. Both addresses use the very same local
byte base with constant nonoverlapping extents. No calls/reads/expressions or
intervening statements cross. This does not infer any structure or ABI.
"""
import copy
import re

from search_rules import Variants, code_only
from native_guard_variants import _canonical, _pointer, _read


def generate(source, repository):
    try:
        rules = Variants(repository)
        a = rules.a
        tree, fn = rules.parse(source)
        aliases = {n.name: n.type for n in rules.parser().parse(rules.prelude).ext
                   if isinstance(n, a.Typedef)}
        nodes = list(rules._nodes(fn))
        if (any(isinstance(n, a.Typedef) and n.name in aliases for n in tree.ext)
                or any(isinstance(n, (a.Typedef, a.Goto, a.Label)) for *_, n in nodes)
                or any(getattr(n, 'quals', ()) or getattr(n, 'storage', ())
                       or getattr(n, 'gcc_attributes', ()) or getattr(n, 'asmlabel', None)
                       for *_, n in nodes)
                or re.search(r'\b(?:volatile|restrict|__asm__|asm)\b', code_only(source))):
            return
        params = fn.decl.type.args.params if fn.decl.type.args else []
        words = {p.name: p for p in params if isinstance(p, a.Decl)
                 and _canonical(p.type, a, aliases) in ('int', 'unsigned int')}
        declarations = [n for *_, n in nodes if isinstance(n, a.Decl) and n.name]
        if len({n.name for n in declarations}) != len(declarations):
            return
        declarations = {d.name: d for d in declarations}
        escaped = {n.expr.name for *_, n in nodes if isinstance(n, a.UnaryOp)
                   and n.op in ('&', '++', '--', 'p++', 'p--') and isinstance(n.expr, a.ID)}
        changed = {n.lvalue.name for *_, n in nodes if isinstance(n, a.Assignment)
                   and isinstance(n.lvalue, a.ID)}
        words = {name: d for name, d in words.items() if name not in escaped | changed}
        if len(words) < 2:
            return
        compounds = [n for *_, n in nodes if isinstance(n, a.Compound)]
        for block in compounds:
            items = block.block_items or []
            for i in range(len(items) - 1):
                left, right = items[i:i + 2]
                if not all(isinstance(n, a.Assignment) and n.op == '='
                           and isinstance(n.rvalue, a.ID) and n.rvalue.name in words
                           for n in (left, right)):
                    continue
                x, y = _read(left.lvalue, a, aliases), _read(right.lvalue, a, aliases)
                if (not x or not y or not isinstance(x[1], a.ID) or not isinstance(y[1], a.ID)
                        or x[1].name != y[1].name or x[1].name in escaped
                        or x[1].name not in declarations
                        or declarations[x[1].name] not in [*params, *(fn.body.block_items or [])]
                        or not _pointer(declarations[x[1].name].type, a, aliases)
                        or any(offset % 4 for offset in (x[2], y[2]))
                        or abs(x[2] - y[2]) < 4):
                    continue
                kinds = [_canonical(x[0], a, aliases), _canonical(y[0], a, aliases),
                         _canonical(words[left.rvalue.name].type, a, aliases),
                         _canonical(words[right.rvalue.name].type, a, aliases)]
                if kinds[0] not in ('int', 'unsigned int') or len(set(kinds)) != 1:
                    continue
                other = copy.deepcopy(tree)
                other_fn = next(n for n in other.ext if isinstance(n, a.FuncDef))
                blocks = [n for *_, n in rules._nodes(other_fn) if isinstance(n, a.Compound)]
                target = blocks[compounds.index(block)]
                target.block_items[i:i + 2] = [target.block_items[i + 1], target.block_items[i]]
                yield 'adjacent-disjoint-input-word-stores', rules._emit(source, other)
                return
    except (ValueError, TypeError, AttributeError, IndexError, KeyError, OSError):
        return
