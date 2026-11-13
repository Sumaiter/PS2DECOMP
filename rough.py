"""Local m2c adapter. All generated input/output stays under build/studio."""
from pathlib import Path
import datetime
import hashlib
import json
import re
import subprocess
import sys
import uuid
import local_process

ADAPTER_VERSION = 16

TYPES = '''typedef signed char s8; typedef unsigned char u8;
typedef short s16; typedef unsigned short u16;
typedef int s32; typedef unsigned int u32;
typedef long long s64; typedef unsigned long long u64;
typedef float f32; typedef double f64;
'''
TYPE = r'(?:const\s+)?(?:void|char|int|float|double|[su](?:8|16|32|64)|f(?:32|64))\b(?:\s*\*)*'
DECL = re.compile(r'^(?:extern\s+)?' + TYPE + r'\s*(func_(?:L\d\d_)?[0-9A-F]{8})\s*\((.*)\)\s*;$')
PARAM = re.compile(r'^' + TYPE + r'(?:\s*[A-Za-z_]\w*)?$')


def context_parameters(text):
    """Accept evidenced primitive prototypes, including terminal C varargs.

    An ellipsis preserves the recorded ABI; it does not supply missing values.
    Empty and void parameter lists remain distinct from variadic declarations.
    """
    parameters = [p.strip() for p in text.split(',')]
    if parameters in ([''], ['void']):
        return True
    if parameters[-1] == '...':
        parameters = parameters[:-1]
        if not parameters:
            return False
    return bool(parameters) and all(p not in ('', 'void', '...') and PARAM.fullmatch(p)
                                    for p in parameters)


def tool(root):
    return next((p for p in (root / 'build/ext/m2c/m2c.py', root / 'tools/ext/m2c/m2c.py') if p.is_file()), None)


def directory(root, name, prefix):
    path = root / 'build/studio' / name / (prefix + '-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12])
    path.mkdir(parents=True, exist_ok=False)
    return path


def context_key(declaration):
    """Primitive ABI identity; parameter spelling and extern are immaterial."""
    match = DECL.fullmatch(declaration)
    if not match:
        return declaration
    params = []
    for parameter in match[2].split(','):
        typed = re.fullmatch('(' + TYPE + r')(?:\s*[A-Za-z_]\w*)?', parameter.strip())
        params.append(re.sub(r'\s+', '', typed[1]) if typed else parameter.strip())
    return (re.sub(r'\s+|\bextern\b', '', declaration[:match.start(1)]), match[1], tuple(params))


def context(contracts, names):
    accepted, skipped = [], []
    for name in sorted(set(names)):
        records = contracts.get(name, [])
        if not records:
            continue
        # Parameter spelling is not a conflicting type contract. Signedness,
        # pointer depth and qualifiers remain significant and are NOT guessed.
        distinct = {}
        for record in records:
            declaration = record['declaration']
            key = context_key(declaration)
            distinct.setdefault(key, declaration)
        if len(distinct) != 1:
            skipped.append(name + ': widersprüchliche Signaturen, nicht automatisch übernommen.')
            continue
        declaration = next(iter(distinct.values()))
        match = DECL.fullmatch(declaration)
        if match and match[1] == name and context_parameters(match[2]):
            accepted.append(declaration)
        else:
            skipped.append(name + ': benutzerdefinierte Typen benötigen manuell ergänzten Kontext.')
    return accepted, skipped


def normalize_raw_cop1(assembly):
    """Recover only exactly encoded unary CVT.W.S rendered as .word by rabbitizer."""
    evidence = []
    pattern = re.compile(r'(?m)^(/\* ([0-9A-F]+) ([0-9A-F]{8}) \*/\s*)\.word\s+0x([0-9A-Fa-f]{8})[^\n]*$')
    def replace(match):
        value = int(match[4], 16)
        # COP1, fmt=S, unused ft=0, fn=CVT.W. No address/function-specific rule.
        if value != int(match[3], 16) or value & 0xFFFF003F != 0x46000024:
            return match[0]
        destination, source = value >> 6 & 31, value >> 11 & 31
        instruction = f'cvt.w.s $f{destination}, $f{source}'
        evidence.append({'address': match[2], 'word': f'{value:08X}', 'instruction': instruction})
        return match[1] + instruction
    return pattern.sub(replace, assembly), evidence


def normalize_ee_registers(assembly):
    """Rabbitizer/GNU O32 names physical8..11 t0..t3; m2c EE calls them a4..a7."""
    aliases = {'t0': 'a4', 't1': 'a5', 't2': 'a6', 't3': 'a7'}
    # The input is a disassembler listing, not arbitrary C or a target to execute.
    names = sorted(set(re.findall(r'\$(t[0-3])\b', assembly)))
    return re.sub(r'\$(t[0-3])\b', lambda m: '$' + aliases[m[1]], assembly), [
        {'physicalRegister': 8 + int(name[1:]), 'listing': '$' + name, 'm2c': '$' + aliases[name]}
        for name in names]


def startup_gp(engine, name=None):
    program = engine.place(name)[0] if name else engine.program
    return engine.validated_gp(program)


def normalize_gp(engine, assembly):
    """Resolve only constant startup GP uses; never guess a modified GP."""
    if re.search(r'\*/\s*\w+\s+\$gp\s*,', assembly):
        return assembly, [], ['Die Funktion verwendet/verändert $gp als Zieloperand; keine automatische GP-Auflösung.']
    if not re.search(r'\$gp\b', assembly):
        return assembly, [], []
    gp = engine.validated_gp(getattr(engine, '_decompile_program', engine.program))
    if gp is None:
        return assembly, [], ['GP-Nachweis fehlt; rohe GP-Zugriffe bleiben offen.']
    resolved = []
    lines = []
    for line in assembly.splitlines():
        match = re.match(r'(\/\* ([0-9A-F]+) [0-9A-F]+ \*/\s*)(\w+)\s+(.*)', line)
        if match:
            prefix, address, op, operands = match.groups()
            immediate = None
            if op in ('addiu', 'daddiu'):
                immediate = re.fullmatch(r'(\$\w+,\s*\$gp,\s*)(-?(?:0x[0-9A-Fa-f]+|\d+))', operands)
            elif op in ('lb', 'lbu', 'lh', 'lhu', 'lw', 'lwu', 'ld', 'lwc1', 'sb', 'sh', 'sw', 'sd', 'swc1'):
                immediate = re.fullmatch(r'(\$\w+,\s*)(-?(?:0x[0-9A-Fa-f]+|\d+))(\(\$gp\))', operands)
            if immediate:
                target = (gp + int(immediate[2], 0)) & 0xFFFFFFFF
                symbol = f'D_{target:08X}'
                suffix = immediate[3] if immediate.lastindex == 3 else ''
                line = prefix + op + ' ' + immediate[1] + '%gp_rel(' + symbol + ')' + suffix
                resolved.append({'instruction': address, 'gp': f'{gp:08X}', 'target': symbol})
        lines.append(line)
    return '\n'.join(lines) + '\n', resolved, []


def forwarding_evidence(engine, name):
    """Read-only instruction evidence; never invent parameter types or values."""
    import struct
    from review02_rules import forwarded_arguments
    place=engine.place(name)[:4]
    raw=engine.check.disc_bytes(place)
    if len(raw)%4 or len(raw)>32768:return {'findings':[], 'warnings':['Argumentfluss: Funktionsfenster nicht unterstützt.']}
    symbols=getattr(engine,'_symbols',{}).get(place[0],{})
    targets={}
    for offset in range(0,len(raw),4):
        word=struct.unpack_from('<I',raw,offset)[0]
        if word>>26!=3:continue
        address=((place[2]+offset+4)&0xf0000000)|((word&0x03ffffff)<<2)
        callee=symbols.get(address)
        if not callee or address in targets:continue
        choices=[p for p in engine.all_places(callee) if p[0]==place[0] and p[2]==address]
        if len(choices)==1 and choices[0][3]<=32768:
            targets[address]=engine.check.disc_bytes(choices[0])
        if len(targets)>=16:break
    return forwarded_arguments(raw,place[2],targets)


def decompile(engine, name, owner=None, timeout=45, *, prepare_only=False):
    """Honor adapter-specific decoding in every editor and search entry point."""
    implementation = getattr(engine, 'decompile', None)
    options = dict(prepare_only=True) if prepare_only else {}
    if implementation is not None:
        return implementation(name, owner=owner, timeout=timeout, **options)
    return _decompile(engine, name, owner, timeout, **options)


def _decompile(engine, name, owner=None, timeout=45, *, prepare_only=False):
    engine.place(name)
    engine.assert_access(name, owner)
    script = tool(engine.root)
    if script is None:
        return {'name': name, 'ok': False, 'source': '', 'warnings': ['Lokales m2c fehlt unter build/ext/m2c oder tools/ext/m2c.'], 'log': ''}
    folder = directory(engine.root, name, 'decompile')
    engine._decompile_program = engine.place(name)[0]
    original = engine.asm(name)
    (folder / 'original.s').write_text(original, encoding='utf-8')
    assembly, decoded = normalize_raw_cop1(original)
    undecoded = re.findall(r'(?m)^/\* ([0-9A-F]+) ([0-9A-F]{8}) \*/\s*\.word\b[^\n]*', assembly)
    assembly, resolved, warnings = normalize_gp(engine, assembly)
    if decoded:
        warnings.append('Rohe COP1-Konvertierung anhand aller Opcode-/Registerfelder als cvt.w.s dekodiert; Gleitkomma-Ausdruck bleibt erhalten.')
    assembly, register_aliases = normalize_ee_registers(assembly)
    (folder / 'input.s').write_text(assembly, encoding='utf-8')
    callees = re.findall(r'\bfunc_(?:L\d\d_)?[0-9A-F]{8}\b', assembly)
    declarations, skipped = context(engine.contracts, [name, *callees])
    warnings += skipped
    try:
        forwarded=forwarding_evidence(engine,name)
    except Exception as exc:
        forwarded={'findings':[], 'warnings':['Argumentfluss konnte nicht belegt werden: '+str(exc)[:300]]}
    if forwarded['findings']:
        warnings.append('Originalinstruktionen belegen weitergereichte Eingangsargumente; Aufrufe mit forwardedArguments im Ergebnis abgleichen. Keine Signaturen automatisch geändert.')
    (folder / 'context.c').write_text(TYPES + '\n'.join(declarations) + '\n', encoding='utf-8')
    command = [sys.executable, str(script), '-t', 'mipsee-gcc-c', '--valid-syntax', '--no-cache', '--context', str(folder / 'context.c'), '-f', name, str(folder / 'input.s')]
    if prepare_only:
        return dict(name=name, directory=str(folder), contextDeclarations=declarations,
                    warnings=warnings, instructionDecodings=decoded, gpResolutions=resolved,
                    registerAliases=register_aliases, undecodedInstructions=undecoded,
                    forwardedArguments=forwarded)
    try:
        run = local_process.run(command, cwd=folder, text=True, encoding='utf-8', errors='replace', timeout=timeout)
        output, log, exitcode = run.stdout, run.stderr, run.returncode
    except subprocess.TimeoutExpired:
        output, log, exitcode = '', 'm2c-Zeitlimit erreicht. Der bisherige Entwurf bleibt erhalten.', -1
    ok = exitcode == 0 and bool(re.search(r'\b' + name + r'\s*\([^;]*\)\s*\{', output))
    if not ok:
        warnings.append('Kein vollständiger Grobentwurf erzeugt. Original und Protokoll stehen im Ergebnisordner.')
    markers = sorted(set(re.findall(r'\b(?:M2C_\w+|saved_reg_\w+)\b', output)))
    self_contract = any(re.search(r'\b' + re.escape(name) + r'\s*\(', declaration) for declaration in declarations)
    abi_problems = ['Nicht dekodierte Originalinstruktion bei ' + address + ' (' + word + '); der Grobentwurf ist nicht vollständig belegt.'
                    for address, word in undecoded]
    definition = re.search(r'\b' + re.escape(name) + r'\s*\(([^()]*)\)\s*\{', output)
    if definition and not self_contract:
        for index, parameter in enumerate(definition[1].split(',')):
            slot = re.search(r'\barg(\d+)\s*$', parameter.strip())
            if slot and int(slot[1]) != index:
                abi_problems.append('m2c hat unbenutzte Argumentpositionen ausgelassen; vollständige Signatur belegen.')
                break
    warnings += abi_problems
    if markers:
        warnings.append('Offene Platzhalter/ankommende Register: ' + ', '.join(markers) + '. Manuell auflösen; keine Ersatzmakros mit Nullwerten ergänzt.')
    warnings.append('Grobentwurf: Typen, Parameter, Datenbesitz und Funktionsgrenzen sind nicht vollständig belegt. Anschließend mit dem Original vergleichen.')
    source = ('#include "common.h"\n\n/* Automatischer m2c-Grobentwurf, kein geprüfter Match.\n * Unbekannte M2C_* und saved_reg_* müssen manuell aufgelöst werden.\n * Signaturen, Datenbesitz und Funktionsgrenzen prüfen.\n */\n' + '\n'.join(declarations) + '\n\n' + output) if ok else ''
    (folder / 'm2c-output.c').write_text(output, encoding='utf-8')
    (folder / 'm2c.log').write_text(log, encoding='utf-8')
    if ok:
        (folder / 'preview.c').write_text(source, encoding='utf-8')
    result = {'name': name, 'ok': ok, 'source': source, 'warnings': warnings, 'log': log[-20000:], 'directory': str(folder),
              'command': command, 'tool': str(script), 'toolHash': hashlib.sha256(script.read_bytes()).hexdigest(),
              'inputHash': hashlib.sha256(assembly.encode('utf-8')).hexdigest(), 'gpResolutions': resolved,
              'contextDeclarations': declarations, 'returncode': exitcode, 'adapterVersion': ADAPTER_VERSION,
              'registerAliases': register_aliases, 'instructionDecodings': decoded, 'undecodedInstructions': undecoded, 'abiProblems': abi_problems,
              'forwardedArguments': forwarded}
    (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def backup(engine, name, source):
    engine.place(name)
    if not isinstance(source, str) or len(source) > 2_000_000:
        raise ValueError('Entwurf fehlt oder ist zu groß.')
    folder = directory(engine.root, name, 'before-rough')
    path = folder / 'draft.c'
    path.write_text(source, encoding='utf-8', newline='\n')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
