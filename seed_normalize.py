"""Small, audited translations of m2c output; never invent missing values/types."""
import re
from search_rules import code_only


def normalize(source, *, generated=False, assembly='', contracts=()):
    notes = []
    # Preserve offsets while ignoring comments and strings when locating macros.
    def masked(text):
        return re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                      lambda m: ' ' * len(m[0]), text, flags=re.S)
    count = 0
    while True:
        text = masked(source)
        matches = list(re.finditer(r'\bM2C_FIELD\s*\(', text))
        if not matches:
            break
        match = matches[-1]  # Expand nested fields inside out, exactly once per argument.
        start = match.end(); depth = 1; cuts = [start]
        for end in range(start, len(text)):
            char = text[end]
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
                if depth == 0:
                    break
            elif char == ',' and depth == 1:
                cuts.append(end + 1)
        else:
            raise ValueError('Unvollständiger M2C_FIELD-Aufruf.')
        if len(cuts) != 3:
            raise ValueError('M2C_FIELD benötigt drei Argumente.')
        expr = source[cuts[0]:cuts[1] - 1].strip()
        ptr = source[cuts[1]:cuts[2] - 1].strip()
        offset = source[cuts[2]:end].strip()
        base = r'(?:const\s+)?(?:[su](?:8|16|32|64|128)|f(?:32|64)|char|short|int|long|float|double|void)'
        callback = re.fullmatch(base + r'(?:\s*\*)?\s*\(\s*\*\s*\*\s*\)\s*\((.*)\)', ptr)
        callback_ok = bool(callback and all(re.fullmatch(base + r'(?:\s*\*)*', parameter.strip()) for parameter in callback[1].split(',')))
        ordinary_ok = bool(re.fullmatch(base + r'(?:\s*\*(?:\s*const\b)?)+', ptr)
                           and not (re.match(r'(?:const\s+)?void\b', ptr) and ptr.count('*') < 2))
        if not ordinary_ok and not callback_ok:
            raise ValueError('M2C_FIELD hat einen noch nicht belegten Zeigertyp: ' + ptr)
        # Exact definition in the installed m2c/m2c_macros.h. Other M2C macros
        # (notably ERROR/TRAP/UNK) are deliberately NOT imported from that header.
        replacement = f'(*({ptr})((s8 *)({expr}) + ({offset})))'
        source = source[:match.start()] + replacement + source[end + 1:]
        count += 1
    if count:
        notes.append(f'{count} M2C_FIELD-Zugriffe gemäß m2c-Makro in typisierte Bytezugriffe übersetzt')
    if generated:
        # m2c's context parser drops pointed-to const qualifiers in definitions.
        # Restore only an otherwise IDENTICAL signature, including parameter names.
        # Never infer a width, pointer level, return type or additional parameter.
        for declaration in contracts:
            contract = re.fullmatch(r'\s*(.+?\b(func_(?:L\d\d_)?[0-9A-F]{8})\s*\([^()]*\))\s*;', declaration)
            if not contract:
                continue
            definition = re.search(r'(?m)^([\w *]+\b' + contract[2] + r'\s*\([^()]*\))\s*\{', source)
            if not definition:
                continue
            canonical = lambda s: re.sub(r'\s+', '', re.sub(r'\bconst\b', '', s))
            if canonical(definition[1]) == canonical(contract[1]) and re.sub(r'\s+', '', definition[1]) != re.sub(r'\s+', '', contract[1]):
                source = source[:definition.start(1)] + contract[1] + source[definition.end(1):]
                notes.append('const-Qualifikatoren aus dem vorhandenen Signaturkontext wiederhergestellt: ' + contract[2])
        # arch_mips.py emits fabsf/fabs for abs.s/abs.d. With no calls in the
        # reference these are synthetic operations, not library calls to preserve.
        if not re.search(r'\b(?:jal|jalr)\b', assembly):
            for instruction, name in [('abs.s', 'fabsf'), ('abs.d', 'fabs')]:
                if re.search(r'\b' + re.escape(instruction) + r'\s', assembly) and re.search(r'\b' + name + r'\s*\(', code_only(source)):
                    source = re.sub(r'\b' + name + r'(?=\s*\()', '__builtin_' + name, source)
                    notes.append(instruction + ': synthetische m2c-Operation als Compiler-Builtin ausgegeben')
    declarations = []
    text = code_only(source)
    # Type names do not establish a compiler ABI. Preserve source/header-owned
    # definitions and let the compiler reject missing types; do not synthesize
    # aliases whose width or signedness can change with the selected profile.
    # Locally documented libm contracts; avoid implicit-int/default promotions.
    for name, declaration in [('fabsf', 'extern f32 fabsf(f32);'), ('fabs', 'extern f64 fabs(f64);')]:
        if re.search(r'\b' + name + r'\s*\(', text) and not re.search(r'\b(?:float|double|f32|f64)\s+' + name + r'\s*\(', text):
            declarations.append(declaration)
            notes.append(name + ': libm-Prototyp ergänzt (src/libm/s_fabs.c)')
    if declarations:
        # Keep an existing first-line cflags marker in place.
        include = re.search(r'^\s*#\s*include\s*"common.h"[^\n]*(?:\n|$)', source, re.M)
        position = include.end() if include else source.find('\n') + 1 if source.startswith('/* cflags:') else 0
        source = source[:position] + '\n'.join(declarations) + '\n' + source[position:]
    unresolved = sorted(set(re.findall(r'\b(?:M2C_\w+|saved_reg_\w+|UNK_\w+)\b', code_only(source))))
    return source, notes, unresolved
