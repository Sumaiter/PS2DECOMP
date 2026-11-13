"""Repair proven m2c byte/unit confusion in generated pointer cursors only.

This is an input recovery pass, not a numeric mutation rule.  Every edited
constant is tied to one original self-add immediate on the named register.
"""
from __future__ import annotations

from pathlib import Path
import re

from search_rules import Variants

_WIDTHS = {
    'void': 1, 'char': 1, 'signed char': 1, 'unsigned char': 1,
    'short': 2, 'short int': 2, 'signed short': 2, 'signed short int': 2,
    'unsigned short': 2, 'unsigned short int': 2,
    'int': 4, 'signed': 4, 'signed int': 4, 'unsigned': 4, 'unsigned int': 4,
    'long': 8, 'long int': 8, 'signed long': 8, 'signed long int': 8,
    'unsigned long': 8, 'unsigned long int': 8,
    'long long': 16, 'long long int': 16, 'signed long long': 16,
    'unsigned long long': 16, 'unsigned long long int': 16,
    'float': 4, 'double': 8,
    's8': 1, 'u8': 1, 's16': 2, 'u16': 2, 's32': 4, 'u32': 4,
    's64': 8, 'u64': 8, 's128': 16, 'u128': 16, 'f32': 4, 'f64': 8,
}
_ALIASES = {'a4': 't0', 'a5': 't1', 'a6': 't2', 'a7': 't3'}
_UNAMBIGUOUS = {'v0', 'v1', 'a0', 'a1', 'a2', 'a3', 'gp', 'sp', 'fp', 's8',
                'ra', 't8', 't9'} | {'s' + str(i) for i in range(8)}
_STORES = {'sb', 'sh', 'sw', 'sd', 'sq', 'swl', 'swr', 'sdl', 'sdr',
           'swc1', 'sdc1', 'sqc2', 'mtc1', 'dmtc1', 'ctc1', 'qmtc2',
           'qmtc2.ni', 'mult', 'multu', 'div', 'divu', 'mthi', 'mtlo'}


def _integer(text):
    text = re.sub(r'[uUlL]+$', '', text.strip())
    if not re.fullmatch(r'[+-]?(?:0[xX][0-9a-fA-F]+|[0-9]+)', text):
        raise ValueError('Keine Ganzzahlkonstante')
    sign = -1 if text.startswith('-') else 1
    raw = text.lstrip('+-')
    return sign * int(raw, 16 if raw.lower().startswith('0x') else 8 if raw.startswith('0') and len(raw) > 1 else 10)


def _instructions(assembly):
    instructions, labels = [], {}
    for line in assembly.splitlines():
        text = re.sub(r'/\*.*?\*/', '', line).split('#', 1)[0].strip()
        if re.fullmatch(r'[\w.$]+:', text):
            labels[text[:-1]] = len(instructions)
        elif text and not text.startswith(('.', 'glabel ')):
            match = re.fullmatch(r'([\w.]+)\s*(.*)', text)
            if match:
                op, args = match.groups()
                instructions.append((op, [x.strip() for x in args.split(',')] if args else []))
    loops = []
    for i, (op, args) in enumerate(instructions):
        if op.startswith('b') and args and args[-1] in labels and labels[args[-1]] <= i:
            # A backward branch executes its following delay instruction too.
            loops.append((labels[args[-1]], min(i + 1, len(instructions) - 1)))
    return instructions, loops


def repair(source, assembly, repository, generated=False):
    """Return (source, notes, blockers), retaining input when proof is absent.

    Only local ``var_<register> +=/-= integer`` pointer cursors from generated
    m2c input qualify.  Register aliases with disputed EE/O32 meanings are held.
    Loops disambiguate pre-loop address construction from actual cursor steps.
    """
    if not generated or not re.search(r'\bvar_\w+\s*(?:\+=|-=|=(?!=))', source):
        return source, [], []
    notes, blockers, changes = [], [], []
    try:
        rules = Variants(Path(repository))
        tree, fn = rules.parse(source)
    except Exception as exc:
        return source, [], ['Zeigerschritt nicht beweisbar: C-AST fehlt: ' + str(exc)]
    a = rules.a
    nodes = [n for *_, n in rules._nodes(fn)]
    declarations = {}
    for node in nodes:
        if isinstance(node, a.Decl) and node.name:
            declarations.setdefault(node.name, []).append(node.type)
    typedefs = {node.name: node.type for node in tree.ext if isinstance(node, a.Typedef)}
    profile = re.findall(r'/\*\s*cflags:.*?\*/', source, re.S)
    sn_widths = not any(re.search(r'@ee(?:29|32|296)', flags) for flags in profile)

    def resolve(kind, seen=None):
        seen = set() if seen is None else seen
        if isinstance(kind, a.TypeDecl) and isinstance(kind.type, a.IdentifierType):
            name = ' '.join(kind.type.names)
            if name in typedefs and name not in seen:
                return resolve(typedefs[name], seen | {name})
        return kind

    def size(kind):
        kind = resolve(kind)
        if isinstance(kind, a.PtrDecl):
            return 4
        if isinstance(kind, a.TypeDecl) and isinstance(kind.type, a.IdentifierType):
            name = ' '.join(kind.type.names)
            width = _WIDTHS.get(name)
            # EE-GCC's long-long width is not SN's128-bit long long.
            return None if width == 16 and not sn_widths else width
        return None

    def linear_step(expr,name):
        if isinstance(expr,a.ID) and expr.name==name:return 0
        if isinstance(expr,a.BinaryOp) and expr.op in ('+','-'):
            prior=linear_step(expr.left,name)
            if prior is None:return None
            value=expr.right
            try:
                if isinstance(value,a.Constant):number=_integer(value.value)
                elif isinstance(value,a.UnaryOp) and value.op in ('+','-') and isinstance(value.expr,a.Constant):number=_integer(value.expr.value)*(-1 if value.op=='-' else 1)
                else:return None
            except ValueError:return None
            return prior+number*(-1 if expr.op=='-' else 1)
        return None
    updates = {}
    for node in nodes:
        if isinstance(node, a.Assignment) and node.op in ('+=', '-=') and isinstance(node.lvalue, a.ID) and re.fullmatch(r'var_\w+', node.lvalue.name):
            updates.setdefault(node.lvalue.name, []).append(node)
        elif isinstance(node,a.Assignment) and node.op=='=' and isinstance(node.lvalue,a.ID) and re.fullmatch(r'var_\w+',node.lvalue.name) and isinstance(node.rvalue,a.BinaryOp) and linear_step(node.rvalue,node.lvalue.name) is not None:
            updates.setdefault(node.lvalue.name,[]).append(node)
    instructions, loops = _instructions(assembly)
    for name, assignments in updates.items():
        types = declarations.get(name, [])
        if len(types) != 1:
            blockers.append(f'{name}: verdeckte/mehrdeutige Deklaration; kein Zeigerschritt geändert.')
            continue
        pointer = resolve(types[0])
        if not isinstance(pointer, a.PtrDecl):
            continue
        if len(assignments) != 1:
            blockers.append(f'{name}: mehrere C-Schritte; Registerzuordnung nicht eindeutig.')
            continue
        width = size(pointer.type)
        if width is None:
            blockers.append(f'{name}: Größe des Zeigerzieltyps ist nicht belegt.')
            continue
        suffix = name[4:]
        register = _ALIASES.get(suffix, suffix if suffix in _UNAMBIGUOUS else None)
        if register is None:
            blockers.append(f'{name}: EE/O32-Registeralias nicht eindeutig belegt.')
            continue
        register = '$' + ('fp' if register == 's8' else register)
        steps = []
        for i, (op, args) in enumerate(instructions):
            if op in ('addiu', 'addi', 'daddiu') and len(args) == 3 and args[0] == args[1] == register:
                try:
                    steps.append((i, _integer(args[2])))
                except ValueError:
                    pass
        in_loop = [(i, step) for i, step in steps if any(lo <= i <= hi for lo, hi in loops)]
        choices = in_loop or steps
        if len(choices) != 1:
            blockers.append(f'{name}: {len(choices)} mögliche Originalschritte; keine Skalierung geraten.')
            continue
        index, byte_step = choices[0]
        intervals = [(lo, hi) for lo, hi in loops if lo <= index <= hi]
        interval = (min(lo for lo, hi in intervals), max(hi for lo, hi in intervals)) if intervals else (0, len(instructions) - 1)
        reused = False
        for i in range(interval[0], interval[1] + 1):
            op, args = instructions[i]
            if i == index:
                continue
            multiply_destination = op in ('mult', 'multu') and len(args) == 3
            if op in ('jal', 'jalr', 'bal') or (args and args[0] == register and (op not in _STORES or multiply_destination) and not op.startswith('b') and op != 'jr'):
                reused = True
        if reused:
            blockers.append(f'{name}: Register wird im relevanten Bereich erneut beschrieben oder aufgerufen; Review nötig.')
            continue
        node = assignments[0]
        literal = node.rvalue
        try:
            if node.op=='=':
                value=linear_step(literal,name)
                if value is None:raise ValueError()
            elif isinstance(literal, a.Constant):
                value = _integer(literal.value)
            elif isinstance(literal, a.UnaryOp) and literal.op in ('+', '-') and isinstance(literal.expr, a.Constant):
                value = _integer(literal.expr.value) * (-1 if literal.op == '-' else 1)
            else:
                raise ValueError()
        except ValueError:
            blockers.append(f'{name}: C-Schritt ist keine einfache Ganzzahlkonstante.')
            continue
        units = value * (-1 if node.op == '-=' else 1)
        if units * width == byte_step:
            notes.append(f'{name}: {units} C-Einheiten × {width} Byte = Originalschritt {byte_step}; bereits korrekt.')
        elif units == byte_step and byte_step and byte_step % width == 0:
            changes.append((node, value // width, name, units, width, byte_step))
        else:
            blockers.append(f'{name}: C-Schritt {units} × {width} Byte passt nicht eindeutig zu Original {byte_step}; keine Skalierung geraten.')
    if blockers:
        return source, notes, blockers
    for node, value, name, old, width, byte_step in changes:
        if node.op=='=':node.op='+='
        node.rvalue = a.Constant('int', str(value)) if value >= 0 else a.UnaryOp('-', a.Constant('int', str(-value)))
        notes.append(f'{name}: m2c-Byteschritt {old} in {old // width} C-Zeigereinheiten umgerechnet ({width} Byte je Einheit, Original {byte_step}).')
    return (rules._emit(source, tree) if changes else source), notes, []
