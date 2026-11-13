"""Library identities backed by the archive map and real function definitions.

This deliberately is not a C preprocessor. Only fdlibm's equivalent ANSI/K&R
definition header pairs are accepted. Unknown
conditions, implicit K&R parameters and mismatched branches stay unresolved.
"""
from pathlib import Path
import copy
import hashlib
import re

from search_rules import Variants, code_only

NATIVE = re.compile(r'func_[0-9A-F]{8}\Z')
MEMBER = re.compile(r'[A-Za-z_]\w*\.o\Z')
SYMBOL = re.compile(r'[A-Za-z_]\w*\Z')
DIRECTIVE = re.compile(r'^\s*#\s*(\w+)(?:\s+(.*?))?\s*$')


def _signature(rules, header, symbol):
    """Parse an actual definition header; reject implicit/variadic ABI guesses."""
    if '#' in header:
        return None
    try:
        tree, fn = rules.parse(header + '\n{}')
    except Exception:
        return None
    a = rules.a
    if len(tree.ext) != 1 or fn.decl.name != symbol:
        return None
    kind = fn.decl.type
    if not isinstance(kind, a.FuncDecl) or kind.args is None:
        return None
    if fn.param_decls:
        names = [p.name for p in kind.args.params if isinstance(p, a.ID)]
        if len(names) != len(kind.args.params) or len(set(names)) != len(names):
            return None
        declared = {p.name: p for p in fn.param_decls}
        if len(declared) != len(fn.param_decls) or set(declared) != set(names):
            return None
        kind.args.params = [copy.deepcopy(declared[n]) for n in names]
        fn.param_decls = None
    if any(not isinstance(p, (a.Decl, a.Typename)) for p in kind.args.params):
        return None
    # Source types are preserved, not inferred from an external header.
    declaration = rules.generator.visit(fn.decl) + ';'
    from rough import context_parameters
    arguments = declaration[declaration.index('(') + 1:declaration.rindex(')')]
    if not context_parameters(arguments):
        return None
    return declaration


def definitions(rules, source, symbol):
    """Find only top-level definitions with an unambiguous primitive header."""
    text = code_only(source.replace('\r\n', '\n').replace('\r', '\n'))
    lines = text.splitlines(keepends=True)
    signature_tokens = {symbol, '__STDC__', 'void', 'char', 'short', 'int', 'long', 'float', 'double',
                        'signed', 'unsigned', 'const', 'volatile', 's8', 's16', 's32', 's64',
                        'u8', 'u16', 'u32', 'u64', 'f32', 'f64'}
    for line in lines:
        directive = DIRECTIVE.match(line.rstrip('\r\n'))
        if directive and directive[1] in ('define', 'undef'):
            macro = re.match(r'[A-Za-z_]\w*', directive[2] or '')
            if macro and macro[0] in signature_tokens:
                return []
    # A second ordinary definition cannot be hidden by a preceding accepted
    # conditional pair. Parse these complete headers too; prototypes and calls
    # do not qualify as top-level typed definitions.
    ordinary = re.compile(r'(?m)^([A-Za-z_][\w \t*]*\b' + re.escape(symbol) + r'\s*\([^;{}#]*\))\s*\{')
    if any(_signature(rules, match[1], symbol) for match in ordinary.finditer(text)):
        return []
    stack, spans, start = [], [], 0
    for i, line in enumerate(lines):
        match = DIRECTIVE.match(line.rstrip('\r\n'))
        if not match:
            continue
        command, expression = match[1], (match[2] or '').strip()
        if command in ('if', 'ifdef', 'ifndef'):
            stack.append(dict(start=i, command=command, expression=expression,
                              outer=len(stack), otherwise=None, invalid=False))
        elif command in ('else', 'elif') and stack:
            item = stack[-1]
            if command != 'else' or item['otherwise'] is not None:
                item['invalid'] = True
            item['otherwise'] = i
        elif command == 'endif' and stack:
            item = stack.pop()
            spans.append((item, i))
    if stack:
        return []
    found, seen_definitions = [], 0
    for item, end in spans:
        if item['otherwise'] is None:
            continue
        tail = ''.join(lines[end + 1:])
        if not tail.lstrip().startswith('{'):
            continue
        ansi = ''.join(lines[item['start'] + 1:item['otherwise']]).strip()
        old = ''.join(lines[item['otherwise'] + 1:end]).strip()
        modern, legacy = _signature(rules, ansi, symbol), _signature(rules, old, symbol)
        if modern or legacy:
            seen_definitions += 1
        if item['outer'] or item['command'] != 'ifdef' or item['expression'] != '__STDC__' or item['invalid']:
            continue
        # A header conditional nested in a body is not an independent function.
        prefix = ''.join(lines[:item['start']])
        # Definitions cannot redefine either a type token or the symbol. Do
        # not attempt macro expansion or guess whether unrelated defines are
        # harmless. The accepted fdlibm sources have only includes here.
        if any((directive := DIRECTIVE.match(line.rstrip('\r\n'))) and directive[1] not in ('include',)
               for line in lines[:item['start']]):
            continue
        prefix = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '', prefix)
        if prefix.count('{') != prefix.count('}'):
            continue
        if modern and modern == legacy:
            found.append((modern, 'equivalent-ANSI-KR-definition-headers'))
    # Ordinary definitions with no intervening preprocessor condition. Library
    # sources in this repository use the header-pair form; avoid expanding the
    # scope to macros or otherwise conditional ordinary definitions for now.
    return found if seen_definitions == 1 else []


def add_contracts(engine):
    path = engine.root / 'config/libm.tsv'
    if not path.is_file():
        return []
    raw = path.read_bytes()
    identities = {}
    for line in raw.decode('utf-8-sig').splitlines():
        if not line or line.startswith('#'):
            continue
        fields = line.split('\t')
        if len(fields) != 4 or not MEMBER.fullmatch(fields[0]) or not SYMBOL.fullmatch(fields[1]):
            continue
        try:
            address, size = int(fields[2], 16), int(fields[3], 16)
        except ValueError:
            continue
        if size <= 0:
            continue
        identities.setdefault(address, set()).add((fields[0], fields[1], size))
    rules = Variants(engine.root)
    records, seen = [], set()
    for row in engine.maps.read_map():
        if row.get('status') != 'match':
            continue
        for target in row.get('names', []):
            if not NATIVE.fullmatch(target) or target not in engine.places:
                continue
            places = set(engine.all_places(target))
            primary = engine.place(target)[:4]
            if not places or primary not in places or not places <= set(row.get('places', [])) or primary[0] != 'main':
                continue
            options = identities.get(primary[2], set())
            if len(options) != 1:
                continue
            member, symbol, size = next(iter(options))
            expected = Path('src/libm') / (member[:-2] + '.c')
            if Path(row.get('file', '')).as_posix() != expected.as_posix() or any(p[3] != size for p in places):
                continue
            source_path = engine.root / expected
            if not source_path.is_file():
                continue
            blob = source_path.read_bytes()
            try:
                pairs = definitions(rules, blob.decode('utf-8-sig'), symbol)
            except (ValueError, UnicodeError):
                continue
            if len(pairs) != 1:
                continue
            prototype, evidence_kind = pairs[0]
            declaration = re.sub(r'\b' + re.escape(symbol) + r'(?=\s*\()', target, prototype, count=1)
            key = target, declaration, str(source_path)
            if key in seen:
                continue
            seen.add(key)
            record = dict(declaration=declaration, status='Belegte PAL-Libm-Matchdefinition; Vertrag vor Abschluss prüfen',
                          source=str(source_path), sourceFunction=symbol, mappedAlias=True,
                          evidence=hashlib.sha256(blob).hexdigest(), libraryMapEvidence=hashlib.sha256(raw).hexdigest(),
                          libraryMember=member, headerEvidence=evidence_kind,
                          notes='Archivsymbol und Matchzeile decken Größe, Adresse und sämtliche Platzierungen ab; ANSI/K&R-Header stimmen vollständig überein.')
            current = engine.contracts.setdefault(target, [])
            if not any(r.get('declaration') == declaration for r in current):
                current.append(record)
            records.append(dict(name=target, **record))
    return records
