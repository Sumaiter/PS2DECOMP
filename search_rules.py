"""Bounded structural C variants. No ABI, numeric, flag or FP algebra mutations."""
from __future__ import annotations
import copy
import hashlib
import re
import sys
from terminal_deref_type import explicit_primitive_dereference_type


def code_only(source):
    # Keep strings intact while removing comments.
    return re.sub(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|/\*.*?\*/|//[^\n]*',
                  lambda m: m[1] if m[1] else ' ', source, flags=re.S)


def source_key(source):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*|\d[\w.]*|\S', code_only(source))
    # The project's cflags comment changes compilation and is NOT merely credit.
    flags = re.findall(r'/\*\s*cflags:.*?\*/', source, re.S)
    return hashlib.sha256('\0'.join(flags + tokens).encode()).hexdigest()


class Variants:
    def __init__(self, repository):
        root = repository / 'build/ext/m2c'
        if not root.is_dir():
            root = repository / 'tools/ext/m2c'
        sys.path.insert(0, str(root))
        # Use the same parser AST as the original reviewed rules. Stock
        # pycparser has different extension/attribute fields and is not a safe
        # interchangeable substitute for these guards.
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent / 'vendor'))
        from m2c_pycparser import c_ast, c_generator, c_parser
        self.a, self.generator, self.parser = c_ast, c_generator.CGenerator(), c_parser.CParser
        self.header_text = (repository / 'include/common.h').read_text(encoding='utf-8')
        self.prelude = '\n'.join(line for line in self.header_text.splitlines()
                                 if line.strip().startswith('typedef '))

    def plain_header(self):
        """Only a complete typedef header, not a guessed preprocessed view.

        An optional ordinary include guard/pragma-once and NULL are harmless.
        Other macros, conditional definitions, nested includes, declarations or
        a provider-modified prelude require a proper preprocessor contract.
        """
        text=code_only(self.header_text)
        if '\\\n' in text or '\\\r\n' in text:return False
        directives=[line.strip() for line in re.findall(r'^\s*#([^\n]*)$',text,re.M)]
        if directives and directives[0]=='pragma once':directives=directives[1:]
        if directives and re.fullmatch(r'ifndef\s+[A-Za-z_]\w*',directives[0]):
            guard=directives[0].split()[1]
            if len(directives)<3 or directives[1]!='define '+guard or directives[-1]!='endif':return False
            directives=directives[2:-1]
        tokens=lambda s:re.findall(r'[A-Za-z_]\w*|\d[\w.]*|\S',s)
        if any(tokens(line)!=tokens('define NULL ((void *)0)') for line in directives):return False
        body=re.sub(r'^\s*#.*$','',text,flags=re.M)
        return tokens(body)==tokens(code_only(self.prelude))

    def parse_unit(self, source):
        """Parse a guarded translation unit for read-only diagnostics.

        Search mutations use parse(), which still requires exactly one function.
        """
        text = code_only(source)
        flags = re.findall(r'/\*\s*cflags\b.*?\*/', source, re.S)
        if flags and (len(flags) != 1 or source.splitlines()[0] != flags[0]
                      or not re.fullmatch(r'/\* cflags: [^\r\n]* \*/', flags[0])):
            raise ValueError('Only one canonical first-line compiler-flags comment is supported.')
        if '\\\n' in source or '\\\r\n' in source:
            raise ValueError('Preprocessor line splices require manual review.')
        directives = re.findall(r'^\s*#.*$', text, re.M)
        if any(not re.fullmatch(r'\s*#\s*include\s*"common.h"\s*', line) for line in directives):
            raise ValueError('AST-Suche unterstützt hier nur common.h; andere Makros/Includes bleiben Reviewfälle.')
        clean = re.sub(r'^\s*#.*$', '', text, flags=re.M)
        prefix = self.parser().parse(self.prelude)
        tree = self.parser().parse(self.prelude + '\n' + clean)
        del tree.ext[:len(prefix.ext)]
        return tree

    def parse(self, source):
        tree = self.parse_unit(source)
        functions = [n for n in tree.ext if isinstance(n, self.a.FuncDef)]
        if len(functions) != 1:
            raise ValueError('Genau eine Funktion für die AST-Suche erforderlich.')
        return tree, functions[0]

    def _nodes(self, node, parent=None, attr=None, index=None):
        yield parent, attr, index, node
        for label, child in node.children():
            m = re.fullmatch(r'(\w+)\[(\d+)\]', label)
            yield from self._nodes(child, node, m[1] if m else label, int(m[2]) if m else None)

    def _emit(self, source, tree):
        comments = re.findall(r'/\*.*?\*/|//[^\n]*', source, re.S)
        flags = [c for c in comments if c.startswith('/* cflags:')]
        comments = [c for c in comments if c not in flags]
        return '\n'.join(flags + ['#include "common.h"'] + comments) + '\n\n' + self.generator.visit(tree) + '\n'

    def generate(self, source, limit=96):
        tree, fn = self.parse(source)
        a = self.a
        nodes = list(self._nodes(fn.body))
        # The emitter also regenerates declarations outside the function.
        # Checking only its body misses __LINE__ etc. in global initializers.
        source_context_builtin = any(isinstance(n, a.ID) and n.name.startswith('__')
                                     for *_, n in self._nodes(tree))
        signature = self.generator.visit(fn.decl)
        declarations = [n for n in tree.ext if isinstance(n, a.Decl) and n.name and not isinstance(n.type, a.FuncDecl)]
        declarations += [n for *_, n in self._nodes(fn) if isinstance(n, a.Decl) and n.name]
        types = {}
        for declaration in declarations:
            spelling = self.generator.visit(declaration.type)
            # Shadowed names are deliberately not inferred.
            types[declaration.name] = spelling if declaration.name not in types else None
        def expression_type(expr):
            if isinstance(expr, a.ID):
                return types.get(expr.name)
            if isinstance(expr, a.Constant):
                return expr.type
            if isinstance(expr, a.UnaryOp) and expr.op == '!':
                return 'int'
            return None
        def same_type(left, right):
            return expression_type(left) is not None and expression_type(left) == expression_type(right)
        integral_types = {'s8', 'u8', 's16', 'u16', 's32', 'u32', 's64', 'u64', 's128', 'u128',
                          'char', 'short', 'int', 'long', 'long long', 'unsigned', 'unsigned int',
                          'unsigned char', 'unsigned short', 'unsigned long', 'unsigned long long'}
        typedefs = {n.name: n.type for n in self.parser().parse(self.prelude).ext
                    if isinstance(n, a.Typedef)}
        typedefs.update({n.name: n.type for n in tree.ext if isinstance(n, a.Typedef)})
        def plain_scalar_type(node, seen=frozenset()):
            # No hidden const/volatile, aggregate or variably modified typedef.
            # A pointer is accepted only when its complete type is equally plain.
            if getattr(node, 'quals', []):
                return False
            if isinstance(node, a.PtrDecl):
                return plain_scalar_type(node.type, seen)
            if not isinstance(node, a.TypeDecl) or not isinstance(node.type, a.IdentifierType):
                return False
            names = node.type.names
            if set(names) <= {'void', 'char', 'short', 'int', 'long', 'signed', 'unsigned', 'float', 'double', '_Bool'}:
                return True
            return (len(names) == 1 and names[0] in typedefs and names[0] not in seen
                    and plain_scalar_type(typedefs[names[0]], seen | {names[0]}))
        scalar_aliases = {}
        for definition in [*self.parser().parse(self.prelude).ext, *tree.ext]:
            if isinstance(definition, a.Typedef):
                scalar_aliases[definition.name] = definition if definition.name not in scalar_aliases else None
        builtin_scalars = {'char', 'signed char', 'unsigned char', 'short', 'short int',
                           'signed short', 'signed short int', 'unsigned short', 'unsigned short int',
                           'int', 'signed', 'signed int', 'unsigned', 'unsigned int',
                           'long', 'long int', 'signed long', 'signed long int', 'unsigned long', 'unsigned long int',
                           'long long', 'long long int', 'signed long long', 'signed long long int',
                           'unsigned long long', 'unsigned long long int', '_Bool', 'float', 'double', 'long double'}
        def canonical_scalar(node, seen=frozenset()):
            # Preserve typedef-level attributes: a name such as "scalar" says
            # nothing about precision, qualifiers or an extension's ABI mode.
            if any(getattr(node, field, None) for field in ('quals', 'align', 'gcc_attributes', 'asmlabel', 'funcspec')):
                return None
            if isinstance(node, a.Typedef):
                return canonical_scalar(node.type, seen)
            if not isinstance(node, a.TypeDecl) or not isinstance(node.type, a.IdentifierType):
                return None
            names = node.type.names
            if len(names) == 1 and names[0] in scalar_aliases:
                name = names[0]
                definition = scalar_aliases[name]
                return canonical_scalar(definition, seen | {name}) if definition is not None and name not in seen else None
            spelling = ' '.join(names)
            return spelling if spelling in builtin_scalars else None
        scalar_types = {}
        for declaration in declarations:
            scalar_types[declaration.name] = canonical_scalar(declaration.type) if declaration.name not in scalar_types else None
        scalar_calls = {node.name: canonical_scalar(node.type.type) for node in tree.ext
                        if isinstance(node, a.Decl) and isinstance(node.type, a.FuncDecl)}
        def terminal_expression_type(expr):
            # Deliberately incomplete: no usual-arithmetic-conversion guesses,
            # implicit function signatures or pointer/aggregate inference.
            if isinstance(expr, a.ID):
                return scalar_types.get(expr.name)
            if isinstance(expr, a.Constant):
                return expr.type if expr.type in integral_types | {'float', 'double', 'long double'} else None
            if isinstance(expr, a.Cast):
                return canonical_scalar(expr.to_type.type)
            if isinstance(expr, a.UnaryOp) and expr.op == '*':
                if not self.plain_header():
                    return None
                return explicit_primitive_dereference_type(
                    expr, a, canonical_scalar,
                    source_typedef_names={n.name for n in tree.ext if isinstance(n, a.Typedef)})
            if isinstance(expr, a.FuncCall) and isinstance(expr.name, a.ID) and expr.name.name not in types:
                return scalar_calls.get(expr.name.name)
            if isinstance(expr, a.UnaryOp) and expr.op in ('+', '-'):
                return terminal_expression_type(expr.expr)
            if isinstance(expr, a.BinaryOp) and expr.op in ('+', '-', '*', '/'):
                left, right = terminal_expression_type(expr.left), terminal_expression_type(expr.right)
                if left == right and left in {'float', 'double', 'long double', 'int', 'unsigned int', 'long', 'unsigned long', 'long long', 'unsigned long long'}:
                    return left
            return None
        def terminal_returns(parent, attr, index, node):
            # Exactly the final two statements of the function, with no label,
            # fall-through code, else body or extra side effects to relocate.
            if (parent is not fn.body or attr != 'block_items' or index != len(parent.block_items) - 2
                    or not isinstance(node, a.If) or node.iffalse is not None
                    or source_context_builtin
                    or any(isinstance(child, a.Typedef) for *_, child in nodes)):
                return None
            left = node.iftrue
            if isinstance(left, a.Compound):
                if len(left.block_items or []) != 1:
                    return None
                left = left.block_items[0]
            right = parent.block_items[index + 1]
            if not isinstance(left, a.Return) or not isinstance(right, a.Return) or left.expr is None or right.expr is None:
                return None
            returned = canonical_scalar(fn.decl.type.type)
            if returned is None or terminal_expression_type(left.expr) != returned or terminal_expression_type(right.expr) != returned:
                return None
            return left, right
        def split_initialization_at(declaration, parent, index):
            # C89 requires every declaration before the first statement. Moving
            # an initializer past another initializer would change evaluation
            # order; crossing a new name could also change its lexical binding.
            # Only cross declarations proven to perform no evaluation.
            if (flow or any(isinstance(child, a.Typedef) for *_, child in nodes)
                    or declaration.storage or declaration.quals
                    or getattr(declaration, 'align', []) or getattr(declaration, 'gcc_attributes', [])
                    or not plain_scalar_type(declaration.type)
                    or isinstance(declaration.init, (a.InitList, a.NamedInitializer))
                    or any(not isinstance(item, (a.Decl, a.Typedef))
                           for item in parent.block_items[:index])):
                return None
            end = index + 1
            crossed = set()
            while end < len(parent.block_items) and isinstance(parent.block_items[end], (a.Decl, a.Typedef)):
                item = parent.block_items[end]
                if (not isinstance(item, a.Decl) or item.init is not None or item.storage
                        or getattr(item, 'align', []) or getattr(item, 'gcc_attributes', [])
                        or item.quals or not plain_scalar_type(item.type)):
                    return None
                crossed.add(item.name)
                end += 1
            referenced = {child.name for *_, child in self._nodes(declaration.init) if isinstance(child, a.ID)}
            if referenced & (crossed | {declaration.name}):
                return None
            return end
        def integer(expr):
            if isinstance(expr, a.Cast):
                return self.generator.visit(expr.to_type.type) in integral_types
            if isinstance(expr, a.BinaryOp) and expr.op in ('+', '-', '*', '&', '|', '^', '<<', '>>'):
                return integer(expr.left) and integer(expr.right)
            return expression_type(expr) in integral_types
        def pure(expr):
            # No loads, calls, increments, assignments, division or volatile objects.
            if isinstance(expr, a.Constant): return True
            if isinstance(expr, a.ID): return types.get(expr.name) in integral_types
            if isinstance(expr, a.Cast): return pure(expr.expr)
            if isinstance(expr, a.BinaryOp): return expr.op in ('+', '-', '*', '&', '|', '^', '<<', '>>') and pure(expr.left) and pure(expr.right)
            return False
        flow = any(isinstance(n, (a.Goto, a.Label)) for *_, n in nodes)
        return_type = fn.decl.type.type
        scalar_return = isinstance(return_type, (a.TypeDecl, a.PtrDecl)) and not any(isinstance(n, (a.Struct, a.Union)) for *_, n in self._nodes(return_type)) and self.generator.visit(return_type) != 'void'
        reserved = {n.name for *_, n in self._nodes(tree) if isinstance(n, (a.ID, a.Decl, a.Typedef))}
        temporary = next('rac4_value_' + str(i) for i in range(10000) if 'rac4_value_' + str(i) not in reserved)
        options = []
        for pos, (parent, attr, index, n) in enumerate(nodes):
            if terminal_returns(parent, attr, index, n) is not None:
                options.append((pos, 'terminale-rueckgaben-verbinden'))
            if isinstance(n, a.If) and n.iffalse is None:
                options.append((pos, 'einseitiges-if-negieren'))
            if isinstance(n, a.UnaryOp) and n.op == '!' and isinstance(n.expr, a.BinaryOp) and n.expr.op in ('<', '<=', '>', '>=', '==', '!='):
                if integer(n.expr.left) and integer(n.expr.right):
                    options.append((pos, 'ganzzahlvergleich-negieren'))
            if isinstance(n, a.BinaryOp) and n.op in ('+', '*', '&', '|', '^') and integer(n.left) and integer(n.right) and pure(n.left) and pure(n.right):
                options.append((pos, 'ganzzahloperanden-tauschen'))
            if isinstance(n, a.Assignment) and isinstance(n.lvalue, a.ID) and types.get(n.lvalue.name) in integral_types:
                if n.op in ('+=', '-=', '*=', '&=', '|=', '^=', '<<=', '>>='):
                    options.append((pos, 'verbundzuweisung-trennen'))
                if n.op == '=' and isinstance(n.rvalue, a.BinaryOp) and n.rvalue.op in ('+', '-', '*', '&', '|', '^', '<<', '>>') and isinstance(n.rvalue.left, a.ID) and n.rvalue.left.name == n.lvalue.name:
                    options.append((pos, 'verbundzuweisung-bilden'))
            if isinstance(n, a.Return) and n.expr is not None and scalar_return and not flow and not re.search(r'\brac4_value_\d+\b', source):
                options.append((pos, 'rueckgabe-ueber-lokale-variable'))
            if isinstance(n, a.If) and n.iffalse is not None:
                options.append((pos, 'if-negieren'))
                left, right = n.iftrue, n.iffalse
                left = left.block_items[0] if isinstance(left, a.Compound) and len(left.block_items or []) == 1 else left
                right = right.block_items[0] if isinstance(right, a.Compound) and len(right.block_items or []) == 1 else right
                if isinstance(left, a.Return) and isinstance(right, a.Return) and left.expr and right.expr and same_type(left.expr, right.expr):
                    options.append((pos, 'return-bedingt'))
                if (isinstance(left, a.Assignment) and isinstance(right, a.Assignment)
                        and left.op == right.op == '=' and isinstance(left.lvalue, a.ID)
                        and isinstance(right.lvalue, a.ID) and left.lvalue.name == right.lvalue.name
                        and same_type(left.rvalue, right.rvalue)):
                    options.append((pos, 'zuweisung-bedingt'))
            if isinstance(n, a.Return) and isinstance(n.expr, a.TernaryOp) and same_type(n.expr.iftrue, n.expr.iffalse):
                options.append((pos, 'return-verzweigen'))
            if isinstance(n, a.Assignment) and n.op == '=' and isinstance(n.lvalue, a.ID) and isinstance(n.rvalue, a.TernaryOp):
                # Only expression statements: never split an assignment used as a value.
                if isinstance(parent, a.Compound) and attr == 'block_items' and same_type(n.rvalue.iftrue, n.rvalue.iffalse):
                    options.append((pos, 'zuweisung-verzweigen'))
            if isinstance(n, a.UnaryOp) and n.op in ('p++', 'p--'):
                if isinstance(parent, a.Compound) and attr == 'block_items' or isinstance(parent, a.For) and attr == 'next':
                    options.append((pos, 'inkrement-voranstellen'))
            if isinstance(n, a.While):
                options.append((pos, 'while-als-for'))
            if isinstance(n, a.For) and not any(isinstance(child, (a.Continue, a.Goto, a.Label)) for *_, child in self._nodes(n.stmt)):
                options.append((pos, 'for-als-while'))
            if isinstance(n, a.Decl) and n.init is not None and isinstance(parent, a.Compound) and attr == 'block_items':
                if split_initialization_at(n, parent, index) is not None:
                    options.append((pos, 'initialisierung-trennen'))
            if isinstance(n, a.Decl) and n.init is None and isinstance(parent, a.Compound) and not n.storage and not n.quals and isinstance(n.type, (a.TypeDecl, a.PtrDecl)):
                # Only across declarations without initializers. No evaluation is moved.
                if not any(isinstance(child, (a.Struct, a.Union, a.ArrayDecl)) or getattr(child, 'quals', []) for *_, child in self._nodes(n.type)):
                    following = parent.block_items[index + 1:]
                    assignment = next((x for x in following if not isinstance(x, a.Decl) or x.init is not None), None)
                    if not flow and isinstance(assignment, a.Assignment) and assignment.op == '=' and isinstance(assignment.lvalue, a.ID) and assignment.lvalue.name == n.name:
                        # Self-referential uninitialized reads cannot be made into initializers.
                        crossed = {x.name for x in following[:following.index(assignment)] if isinstance(x, a.Decl)} | {n.name}
                        if not any(isinstance(x, a.ID) and x.name in crossed for *_, x in self._nodes(assignment.rvalue)):
                            options.append((pos, 'initialisierung-verbinden'))
                    if following and isinstance(following[0], a.Decl) and following[0].init is None and not following[0].storage and isinstance(following[0].type, a.TypeDecl) and isinstance(following[0].type.type, a.IdentifierType) and not following[0].quals:
                        # Exclude typedef-dependent declarations and never reorder across code.
                        if isinstance(n.type, a.TypeDecl) and isinstance(n.type.type, a.IdentifierType):
                            options.append((pos, 'lokale-deklarationen-tauschen'))
        seen = {source_key(source)}
        for pos, rule in options[:limit]:
            changed = copy.deepcopy(tree)
            f = next(n for n in changed.ext if isinstance(n, a.FuncDef))
            parent, attr, index, n = list(self._nodes(f.body))[pos]
            replacement = n
            terminal_declaration = None
            if rule == 'terminale-rueckgaben-verbinden':
                left = n.iftrue.block_items[0] if isinstance(n.iftrue, a.Compound) else n.iftrue
                right = parent.block_items[index + 1]
                typ = copy.deepcopy(f.decl.type.type)
                typ.declname = temporary
                terminal_declaration = a.Decl(name=temporary, quals=[], align=[], storage=[], funcspec=[], gcc_attributes=[], type=typ, init=None, bitsize=None, asmlabel=None)
                # Keep the original comparison verbatim, including its NaN
                # behavior. Evaluate only the selected return expression once.
                replacement = a.If(n.cond, a.Compound([a.Assignment('=', a.ID(temporary), left.expr)]),
                                   a.Compound([a.Assignment('=', a.ID(temporary), right.expr)]))
                parent.block_items[index + 1] = a.Return(a.ID(temporary))
            elif rule == 'einseitiges-if-negieren':
                n.cond, n.iftrue, n.iffalse = a.UnaryOp('!', n.cond), a.Compound([]), n.iftrue
            elif rule == 'ganzzahlvergleich-negieren':
                opposite = {'<': '>=', '<=': '>', '>': '<=', '>=': '<', '==': '!=', '!=': '=='}
                n.expr.op = opposite[n.expr.op]
                replacement = n.expr
            elif rule == 'ganzzahloperanden-tauschen':
                n.left, n.right = n.right, n.left
            elif rule == 'verbundzuweisung-trennen':
                op, n.op = n.op[:-1], '='
                n.rvalue = a.BinaryOp(op, copy.deepcopy(n.lvalue), n.rvalue)
            elif rule == 'verbundzuweisung-bilden':
                n.op, n.rvalue = n.rvalue.op + '=', n.rvalue.right
            elif rule == 'rueckgabe-ueber-lokale-variable':
                typ = copy.deepcopy(f.decl.type.type)
                leaf = typ
                while not isinstance(leaf, a.TypeDecl): leaf = leaf.type
                leaf.declname = temporary
                decl = a.Decl(name=temporary, quals=[], align=[], storage=[], funcspec=[], gcc_attributes=[], type=typ, init=n.expr, bitsize=None, asmlabel=None)
                replacement = a.Compound([decl, a.Return(a.ID(temporary))])
            elif rule == 'initialisierung-verbinden':
                assignment = next(x for x in parent.block_items[index + 1:] if not isinstance(x, a.Decl) or x.init is not None)
                n.init = assignment.rvalue
                parent.block_items.remove(assignment)
            elif rule == 'lokale-deklarationen-tauschen':
                parent.block_items[index], parent.block_items[index + 1] = parent.block_items[index + 1], n
                replacement = parent.block_items[index]
            elif rule == 'if-negieren':
                n.cond, n.iftrue, n.iffalse = a.UnaryOp('!', n.cond), n.iffalse, n.iftrue
            elif rule in ('return-bedingt', 'zuweisung-bedingt'):
                l, r = n.iftrue, n.iffalse
                l = l.block_items[0] if isinstance(l, a.Compound) else l
                r = r.block_items[0] if isinstance(r, a.Compound) else r
                replacement = (a.Return(a.TernaryOp(n.cond, l.expr, r.expr)) if rule == 'return-bedingt'
                               else a.Assignment('=', l.lvalue, a.TernaryOp(n.cond, l.rvalue, r.rvalue)))
            elif rule == 'return-verzweigen':
                t = n.expr
                replacement = a.If(t.cond, a.Return(t.iftrue), a.Return(t.iffalse))
            elif rule == 'zuweisung-verzweigen':
                t = n.rvalue
                replacement = a.If(t.cond, a.Assignment('=', copy.deepcopy(n.lvalue), t.iftrue),
                                   a.Assignment('=', n.lvalue, t.iffalse))
            elif rule == 'inkrement-voranstellen':
                n.op = n.op[1:]
            elif rule == 'while-als-for':
                replacement = a.For(None, n.cond, None, n.stmt)
            elif rule == 'for-als-while':
                # Keep the old body's lexical scope separate from the increment.
                loop = a.While(n.cond or a.Constant('int', '1'), a.Compound([n.stmt] + ([n.next] if n.next else [])))
                init = n.init.decls if isinstance(n.init, a.DeclList) else [n.init] if n.init else []
                replacement = a.Compound(init + [loop])
            elif rule == 'initialisierung-trennen':
                end = split_initialization_at(n, parent, index)
                assert end is not None
                init, n.init = n.init, None
                parent.block_items.insert(end, a.Assignment('=', a.ID(n.name), init))
            if parent is None:
                continue
            if index is None:
                setattr(parent, attr, replacement)
            else:
                getattr(parent, attr)[index] = replacement
            if terminal_declaration is not None:
                f.body.block_items.insert(0, terminal_declaration)
            assert self.generator.visit(f.decl) == signature
            emitted = self._emit(source, changed)
            key = source_key(emitted)
            if key not in seen:
                seen.add(key)
                yield rule, emitted
