"""Early scalar metadata, justified by immutable compiler and native GP evidence.

This deliberately supports only plain f32 extern reads in level functions. It
does not guess types, change flags, resolve literal pools, or approve a match.
Missing or incompatible source/object/input evidence means no search child.
"""
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import struct

LABEL = 'rac3-native-gp-extern-size'
SYMBOL = re.compile(r'D_([0-9A-F]{8})(_sda)?\Z')
FUNCTION = re.compile(r'func_L\d\d_[0-9A-F]{8}\Z')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _clean(text):
    from search_rules import code_only
    return code_only(text)


def _source_shape(engine, name, source, rules):
    if not isinstance(source, str) or not FUNCTION.fullmatch(name):
        return None
    # Parse the current header, not a provider-supplied substitute prelude.
    header = (engine.root / 'include/common.h').read_text(encoding='utf-8')
    if getattr(rules, 'header_text', None) != header:
        return None
    clean_header = _clean(header)
    lines = re.findall(r'^\s*#.*$', clean_header, re.M)
    allowed = (r'\s*#\s*ifndef\s+COMMON_H\s*', r'\s*#\s*define\s+COMMON_H\s*',
               r'\s*#\s*include\s+"include_asm.h"\s*', r'\s*#\s*endif\s*')
    if any(not any(re.fullmatch(pattern, line) for pattern in allowed) for line in lines):
        return None
    body = re.sub(r'^\s*#.*$', '', clean_header, flags=re.M)
    typedef = r'\s*typedef\s+((?:(?:signed|unsigned)\s+)?(?:char|short|int|long(?:\s+long)?)|float|double)\s+([A-Za-z_]\w*)\s*;'
    pairs = re.findall(typedef, body)
    if re.sub(typedef, '', body).strip() or dict((n, ty) for ty, n in pairs).get('f32') != 'float':
        return None
    if sum(n == 'f32' for _, n in pairs) != 1:
        return None
    # The included assembler-only macros must not hide a scalar type modifier.
    asm_header = engine.root / 'include/include_asm.h'
    if asm_header.is_file():
        included = _clean(asm_header.read_text(encoding='utf-8'))
        macro_names = re.findall(r'^\s*#\s*define\s+([A-Za-z_]\w*)', included, re.M)
        known = {'INCLUDE_ASM_H', 'INCLUDE_ASM', 'ASM_FUNC', 'LINKER_REMNANT',
                 'INCLUDE_RODATA', 'TEXT_PADDING', 'TEXT_FILL'}
        if set(macro_names) - known or re.search(r'^\s*#\s*(?:include|undef)\b', included, re.M):
            return None
    if re.search(r'/\*\s*cflags\b', source) or re.search(r'\b(?:volatile|__attribute__|__asm__|asm)\b', _clean(source)):
        return None
    try:
        tree, fn = rules.parse(source)
    except Exception:
        return None
    if fn.decl.name != name:
        return None
    a = rules.a
    nodes = list(rules._nodes(tree))
    if any(isinstance(n, (a.If, a.Switch, a.For, a.While, a.DoWhile, a.Goto, a.Label))
           for *_, n in nodes):
        return None
    declarations = [n for *_, n in nodes if isinstance(n, a.Decl) and n.name]
    counts = Counter(d.name for d in declarations)
    result = []
    for declaration in tree.ext:
        if not isinstance(declaration, a.Decl) or not SYMBOL.fullmatch(declaration.name or ''):
            continue
        ty = declaration.type
        if (counts[declaration.name] != 1 or declaration.storage != ['extern']
                or declaration.init is not None or declaration.quals
                or not isinstance(ty, a.TypeDecl) or ty.quals
                or not isinstance(ty.type, a.IdentifierType) or ty.type.names != ['f32']
                or any(getattr(n, key, None) for n in (declaration, ty)
                       for key in ('gcc_attributes', 'asmlabel', 'align'))):
            continue
        reads = [(parent, n) for parent, _, _, n in rules._nodes(fn.body)
                 if isinstance(n, a.ID) and n.name == declaration.name]
        if not reads:
            continue
        # An address, write, increment, sizeof or call target is not a scalar read.
        if any((isinstance(p, a.UnaryOp) and p.op not in ('+', '-', '!'))
               or (isinstance(p, a.Assignment) and p.lvalue is n)
               or (isinstance(p, a.FuncCall) and p.name is n)
               for p, n in reads):
            continue
        result.append((declaration.name, len(reads)))
    return result or None


def _places(engine, name):
    primary = tuple(engine.place(name)[:4])
    places = [tuple(p) for p in engine.all_places(name)]
    if (not places or places[0] != primary or places.count(primary) != 1
            or len(set(places)) != len(places)):
        raise ValueError('Ambiguous placement identity')
    for program, section, address, size in places:
        if (not re.fullmatch(r'level_\d\d', program) or section != '.text'
                or type(address) is not int or type(size) is not int
                or address % 4 or size <= 0 or size % 4 or address < 0
                or address + size > 0x100000000):
            raise ValueError('Unsupported placement')
    return places


def _current_flags(engine, name):
    owner = engine.owners[name]
    owners = engine.owner_paths.get(name, {owner})
    if set(owners) != {owner}:
        raise ValueError('Multiple source contexts')
    entries = []
    for line in (engine.root / 'config/levels/text_parts.txt').read_text(encoding='utf-8').splitlines():
        if line and not line.startswith('#'):
            path, flags = line.split('\t', 1)
            if path == owner:
                entries.append(flags.split())
    if len(entries) != 1 or '@ps2as' not in entries[0] or '-G8' not in entries[0]:
        raise ValueError('Fixed source profile not supported')
    return entries[0]


def _inputs(engine, name, places):
    root = engine.root
    required = {root / 'include/common.h', root / engine.owners[name],
                root / 'config/levels/text_parts.txt'}
    required.update((root / 'include').rglob('*.h'))
    required.update((root / 'tools').glob('*.py'))
    for pattern in ('*.tsv', '*.txt'):
        required.update((root / 'config').rglob(pattern))
    for pattern in ('*.exe', 'specs'):
        required.update((root / 'build/toolchains').rglob(pattern))
    required.update(engine.program_path(p[0]) for p in places)
    if not list((root / 'tools').glob('*.py')) or not list((root / 'build/toolchains').rglob('*.exe')):
        raise ValueError('Compiler/checker identity missing')
    return {p.resolve(): _sha(p.read_bytes()) for p in required}


def _record_is_pinned(engine, name, snapshot, source_hash, object_hash, inputs):
    automatic = engine.root / 'build/autodecomp'
    sessions = list(automatic.glob('*')) + list(automatic.glob('*/history/*'))
    for session in sessions:
        history = session / 'jobs' / name / 'attempts.jsonl'
        if not history.is_file():
            continue
        matches = []
        for line in history.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if (record.get('sourceHash') == source_hash and record.get('objectHash') == object_hash
                    and Path(record.get('snapshot', '')).resolve() == snapshot):
                matches.append(record)
        if not matches:
            continue
        data = json.loads((session / 'session.json').read_text(encoding='utf-8'))
        if data.get('version') != 'pal':
            continue
        pins = {Path(path).resolve(): pin.get('sha256') for path, pin in data['pins'].items()}
        worker_hash = _sha(Path(__file__).with_name('check_worker.py').read_bytes())
        worker_pins = [pin for path, pin in pins.items()
                       if path.name == 'check_worker.py' and path.parent.name == 'RAC3_PAL']
        if (worker_pins and set(worker_pins) == {worker_hash}
                and all(pins.get(path) == digest for path, digest in inputs.items())):
            return True
    return False


def _sizes(engine, name, source, places, flags, inputs, candidates):
    from elftools.elf.elffile import ELFFile
    from elftools.elf.relocation import RelocationSection
    digest = _sha(source.encode('utf-8'))
    observed = {symbol: set() for symbol, _ in candidates}
    for result_path in (engine.root / 'build/studio' / name).glob('*/result.json'):
        result = json.loads(result_path.read_text(encoding='utf-8'))
        if result.get('sourceHash') != digest:
            continue
        folder = result_path.parent
        snapshot = (folder / 'candidate.c').resolve()
        if snapshot.read_text(encoding='utf-8') != source or result.get('name') != name:
            continue
        meta = result.get('rac3Evidence', {})
        obj = (folder / 'candidate.o').read_bytes()
        if (_sha(obj) != meta.get('objectHash') or Path(result['snapshot']).resolve() != snapshot
                or meta.get('flags') != flags or meta.get('address') != places[0][2]
                or meta.get('retailSize') != places[0][3] or meta.get('target') != places[0][0]
                or meta.get('unresolvedRelocations') or meta.get('dataSections') or meta.get('extraDefinitions')
                or _sha((folder / 'source.bin').read_bytes()) != meta.get('resolvedBodyHash')
                or (folder / 'target.bin').read_bytes() != engine.check.disc_bytes(places[0])):
            continue
        if not _record_is_pinned(engine, name, snapshot, digest, _sha(obj), inputs):
            continue
        elf = ELFFile(io.BytesIO(obj))
        if elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_MIPS':
            continue
        text_index = elf.get_section_index('.text')
        text = elf.get_section(text_index).data()
        symtab = elf.get_section_by_name('.symtab')
        functions = [s for s in symtab.iter_symbols() if s.name == name]
        if (len(functions) != 1 or functions[0]['st_shndx'] != text_index
                or functions[0]['st_value'] != 0 or functions[0]['st_size'] != meta.get('compiledSize')):
            continue
        for symbol, count in candidates:
            symbols = [s for s in symtab.iter_symbols() if s.name == symbol]
            if len(symbols) != 1 or symbols[0]['st_shndx'] != 'SHN_UNDEF':
                continue
            refs = []
            for sec in elf.iter_sections():
                if isinstance(sec, RelocationSection):
                    table = elf.get_section(sec['sh_link'])
                    for rel in sec.iter_relocations():
                        if table.get_symbol(rel['r_info_sym']).name == symbol:
                            refs.append((sec['sh_info'], rel['r_offset'], rel['r_info_type']))
            lows = [r for r in refs if r[2] == 6]
            if (len(lows) != count or not any(r[2] == 5 for r in refs)
                    or any(section != text_index or kind not in (5, 6) or offset + 4 > functions[0]['st_size']
                           or struct.unpack_from('<I', text, offset)[0] & 0xFFFF
                           for section, offset, kind in refs)
                    or any(struct.unpack_from('<I', text, offset)[0] >> 26 != 49 for _, offset, _ in lows)):
                continue
            observed[symbol].add(symbols[0]['st_size'])
    return {symbol for symbol, sizes in observed.items() if sizes == {4}}


def _native_matches(engine, places, symbol, count):
    from ee_common import complete_cfg
    from native_load_before_clear import _writes
    address, follows_gp = SYMBOL.fullmatch(symbol).groups()
    for place in places:
        engine.assert_reference(place[0])
        gp = engine.validated_gp(place[0])
        expected = int(address, 16) + (gp - 0x1DC8B0 if follows_gp else 0)
        raw = engine.check.disc_bytes(place)
        if len(raw) != place[3] or not complete_cfg(raw, place[2]):
            return False
        words = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
        found = 0
        constants = {}
        for word in words:
            op, rs, rt = word >> 26, word >> 21 & 31, word >> 16 & 31
            imm = (word & 0x7FFF) - (word & 0x8000)
            # R5900 MULT/MULTU may additionally write rd. The reused decoder
            # models ordinary MIPS HI/LO effects, so do not infer GP/address
            # provenance across this EE extension.
            if op == 0 and word & 63 in (24, 25) and (word >> 11 & 31):
                return False
            writes = _writes(word)
            if 28 in writes:
                return False
            if op == 49:
                if rs == 28 and (gp + imm) & 0xFFFFFFFF == expected:
                    found += 1
                elif rs in constants and (constants[rs] + imm) & 0xFFFFFFFF == expected:
                    return False  # Mixed native absolute/GP forms stay manual.
            before = dict(constants)
            for register in writes:
                constants.pop(register, None)
            if op == 15:
                constants[rt] = (word & 0xFFFF) << 16
            elif op in (9, 25) and rs in before:
                constants[rt] = (before[rs] + imm) & 0xFFFFFFFF
        if found != count:
            return False
    return True


def children(engine, name, source, variants):
    """Offer one metadata-only child; ordinary search budgets and checks apply."""
    try:
        if engine.version != 'pal':
            return []
        shape = _source_shape(engine, name, source, variants)
        if not shape:
            return []
        places = _places(engine, name)
        flags = _current_flags(engine, name)
        inputs = _inputs(engine, name, places)
        proven = _sizes(engine, name, source, places, flags, inputs, shape)
        symbols = [symbol for symbol, count in shape
                   if symbol in proven and _native_matches(engine, places, symbol, count)]
        if not symbols:
            return []
        hints = ''.join('__asm__(".extern %s, 4");\n' % symbol for symbol in symbols)
        return [(LABEL, hints + source)]
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError):
        return []
