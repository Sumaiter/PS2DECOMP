"""Manifest-driven ELF32 little-endian MIPS integration for PS2 EE projects.

Only supported, fully resolved relocations can pass the technical gate. Unknown
ABIs, data ownership and semantics remain independent review obligations.
"""
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import sys
from types import SimpleNamespace
import uuid

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
import project_config as config
from project_claims import ClaimAPI
from workbench_core import Engine as EditorEngine, BLOCKS

APP = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def signed16(value):
    return (value & 0x7fff) - (value & 0x8000)


class Engine(EditorEngine):
    ELFFile = staticmethod(ELFFile)

    @staticmethod
    def version_availability(repository):
        data = config.load(repository)
        ready = all(config.path(Path(repository).resolve(), p['elf']).is_file() for p in data['programs'].values())
        return [dict(id=data['version'], label=data['name'] + ' · ' + data['version'], available=ready,
                     reason='' if ready else 'Reference ELF files are missing.')]

    def __init__(self, repository, version=None):
        self.root = Path(repository).resolve()
        self.config = config.load(self.root)
        self.version = self.config['version']
        if version is not None and version != self.version:
            raise ValueError('Session version differs from ps2decomp.json; use the correct workspace.')
        self.program = self.config['mainProgram']
        self.manual_owner = 'ps2decomp-editor-' + uuid.uuid4().hex
        self.example_cache = None
        self.places, self.aliases, self.remnants, self.contracts = {}, {}, set(), {}
        self._symbols, self._matched = {}, {}
        inventory = json.loads(config.path(self.root, self.config['functions']).read_text(encoding='utf-8-sig'))
        for row in inventory:
            name = row['name']
            if not config.FUNCTION.fullmatch(name) or name in self.places:
                raise ValueError('Invalid or duplicate function identity: ' + name)
            placements = []
            for p in row['placements']:
                program, section = p['program'], p.get('section', '.text')
                start, size = config.integer(p['address']), config.integer(p['size'])
                if program not in self.config['programs'] or start < 0 or size < 4 or start % 4 or size % 4 or start + size > 2**32:
                    raise ValueError('Invalid function placement: ' + name)
                placements.append((program, section, start, size))
                symbols = self._symbols.setdefault(program, {})
                if start in symbols and symbols[start] != name:
                    raise ValueError('Ambiguous function identity at the same program address.')
                symbols[start] = name
            if not placements or len(set(placements)) != len(placements):
                raise ValueError('A function needs unique, evidenced placements.')
            kind = 'remnant' if row.get('remnant', False) else 'function'
            self.places[name] = (*placements[0], kind)
            self.aliases[name] = placements
            if kind == 'remnant':
                self.remnants.add(name)
            if row.get('status') in ('match', 'published'):
                self._matched[name] = dict(name=name, file=row.get('source', ''), status='match', places=placements)
            if row.get('declaration'):
                if not row.get('evidence'):
                    raise ValueError(name + ': declarations require evidence references.')
                self.contracts[name] = [dict(declaration=row['declaration'], evidence=row['evidence'],
                    source=self.config['functions'], status='Project contract; independently review ABI', notes='')]
        self.check = SimpleNamespace(disc_bytes=self.disc_bytes, places=lambda: self.places)
        self.maps = SimpleNamespace(read_map=lambda: list(self._matched.values()))
        self.try_func = SimpleNamespace(compiled=self.compiled)
        self.retail = SimpleNamespace(fields=self.fields, reloc_masks=self.reloc_masks)
        self.levels = SimpleNamespace(program_path=self.program_path)

    def place(self, name):
        if name not in self.places:
            raise ValueError('Unknown catalogued function: ' + str(name))
        return self.places[name]

    def all_places(self, name):
        self.place(name)
        return self.aliases[name]

    def program_path(self, program):
        return config.path(self.root, self.config['programs'][program]['elf'])

    def reference_identity(self, place):
        program = place[0]
        return dict(path=self.program_path(program), algorithm='sha256',
                    digest=self.config['programs'][program]['sha256'],
                    eeSections=self.config['programs'][program].get('eeSections'),
                    eeSectionsEvidence=self.config['programs'][program].get('eeSectionsEvidence'))

    def assert_reference(self, program):
        path = self.program_path(program)
        blob = path.read_bytes()
        elf = ELFFile(io.BytesIO(blob))
        if (hashlib.sha256(blob).hexdigest() != self.config['programs'][program]['sha256'].lower()
                or elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_MIPS'):
            raise ValueError(program + ': reference hash or ELF32 little-endian MIPS format differs.')

    def disc_bytes(self, place):
        program, section, address, size = place[:4]
        self.assert_reference(program)
        elf = ELFFile(io.BytesIO(self.program_path(program).read_bytes()))
        sec = elf.get_section_by_name(section)
        if sec is None or not sec['sh_addr'] <= address < address + size <= sec['sh_addr'] + sec['sh_size']:
            raise ValueError('Function window lies outside its reference section.')
        if sec['sh_type'] == 'SHT_NOBITS' or not sec['sh_flags'] & 4:
            raise ValueError('Function reference must be executable file-backed code.')
        offset = address - sec['sh_addr']
        return sec.data()[offset:offset + size]

    def validated_gp(self, program):
        self.assert_reference(program)
        p = self.config['programs'][program]
        if 'gp' not in p or not p.get('gpEvidence'):
            raise ValueError(program + ': GP needs a value and an evidence reference in ps2decomp.json.')
        gp = config.integer(p['gp'])
        if not 0 <= gp < 2**32:
            raise ValueError('GP is outside the EE address space.')
        return gp

    def inventory_state(self):
        api = ClaimAPI(self.root)
        holders = {p.name: api.holder(p.name) for p in api.DIR.glob('func_*') if config.FUNCTION.fullmatch(p.name)}
        matched = {p[:3] for row in self._matched.values() for p in row['places']}
        return dict(self._matched), matched, {}, holders

    def assert_access(self, name, owner=None):
        self.place(name)
        holder = ClaimAPI(self.root).holder(name)
        if holder and holder not in (owner, self.manual_owner):
            raise ValueError(name + ': reserved by ' + holder + '. Explicit review handoff is required.')

    @contextmanager
    def manual_lease(self, name):
        self.assert_access(name)
        import project_coordination
        project_coordination.assert_free(self, [name])
        api = ClaimAPI(self.root)
        if not api.claim(self.manual_owner, name):
            raise ValueError('Function was claimed by another worker.')
        try:
            yield
        finally:
            api.release(self.manual_owner, name)

    def status(self):
        try:
            config.verified_inputs(self.root, self.config)
            compiler = True
        except (OSError, ValueError):
            compiler = False
        return dict(repository=str(self.root), python=sys.executable, compiler=compiler,
                    retail=all(self.program_path(p).is_file() for p in self.config['programs']),
                    functions=len(self.places), contracts=len(self.contracts), blocks=BLOCKS)

    def asm(self, name):
        import rabbitizer
        place = self.place(name)
        raw = self.disc_bytes(place)
        labels, records = set(), []
        for off in range(0, len(raw), 4):
            address = place[2] + off
            value = struct.unpack_from('<I', raw, off)[0]
            ins = rabbitizer.Instruction(value, address, rabbitizer.InstrCategory.R5900)
            text = ins.disassemble()
            if ins.isBranch():
                target = ins.getBranchVramGeneric()
                labels.add(target)
                text = re.sub(r'0x[0-9A-Fa-f]+\s*$', '.L%08X' % target, text)
            elif value >> 26 in (2, 3):
                target = ((address + 4) & 0xf0000000) | ((value & 0x3ffffff) << 2)
                symbol = self._symbols.get(place[0], {}).get(target)
                if symbol:
                    text = text.split()[0] + ' ' + symbol
            records.append((address, value, text))
        out = ['glabel ' + name]
        for address, value, text in records:
            if address in labels:
                out.append('.L%08X:' % address)
            out.append('/* %08X %08X */  %s' % (address, value, text))
        return '\n'.join(out) + '\n'

    def symbol_address(self, symbol, program='main'):
        program = self.program if program == 'main' and program not in self.config['programs'] else program
        explicit = self.config['programs'][program].get('symbols', {})
        if symbol in explicit:
            return config.integer(explicit[symbol])
        if symbol in self.aliases:
            matches = {p[2] for p in self.all_places(symbol) if p[0] == program}
            if len(matches) == 1:
                return matches.pop()
        return None

    def decompile(self, name, owner=None, timeout=45, *, prepare_only=False):
        import rough
        self._decompile_program = self.place(name)[0]
        return rough._decompile(self, name, owner, timeout, prepare_only=prepare_only)

    @staticmethod
    def reloc_masks(elf, index):
        masks = {}
        for sec in elf.iter_sections():
            if isinstance(sec, RelocationSection) and sec['sh_info'] == index:
                for rel in sec.iter_relocations():
                    masks[rel['r_offset']] = {2: 0xffffffff, 4: 0x3ffffff, 5: 0xffff, 6: 0xffff, 7: 0xffff}.get(rel['r_info_type'], 0xffffffff)
        return masks

    @staticmethod
    def fields(code, masks, start):
        result = bytearray(code)
        for offset, mask in masks.items():
            off = offset - start
            if 0 <= off <= len(result) - 4:
                value = struct.unpack_from('<I', result, off)[0]
                struct.pack_into('<I', result, off, value & ~mask)
        return bytes(result)

    def compiled(self, obj, name):
        elf = ELFFile(io.BytesIO(Path(obj).read_bytes()))
        if elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_MIPS' or elf['e_type'] != 'ET_REL':
            raise ValueError('Compiler must produce an ELF32 little-endian MIPS relocatable object.')
        symbols = elf.get_section_by_name('.symtab')
        matches = [] if symbols is None else [s for s in symbols.iter_symbols() if s.name == name and isinstance(s['st_shndx'], int)]
        if len(matches) != 1 or matches[0]['st_info']['type'] != 'STT_FUNC':
            raise ValueError('Object must define the requested function once with a function size.')
        fn = matches[0]
        sec = elf.get_section(fn['st_shndx'])
        start, size = fn['st_value'], fn['st_size']
        if not size or size % 4 or start % 4 or start + size > sec['sh_size'] or not sec['sh_flags'] & 4:
            raise ValueError('Invalid compiled function window.')
        return sec.data()[start:start + size], self.reloc_masks(elf, fn['st_shndx']), start

    def relocations(self, obj, code, start, want, place):
        elf = ELFFile(io.BytesIO(Path(obj).read_bytes()))
        symbols = elf.get_section_by_name('.symtab')
        fn = next(s for s in symbols.iter_symbols() if s['st_info']['type'] == 'STT_FUNC' and s['st_value'] == start and isinstance(s['st_shndx'], int))
        checked, unresolved, covered, highs = [], [], [], {}
        def fail(offset, reason):
            unresolved.append(dict(offset=offset, reason=reason))
        def check(offset, kind, symbol, actual, mask):
            expected = struct.unpack_from('<I', want, offset)[0]
            checked.append(dict(offset=offset, type=kind, symbol=symbol, expected=hex(expected & mask),
                                actual=hex(actual & mask), ok=(expected & mask) == (actual & mask)))
        for sec in elf.iter_sections():
            if not isinstance(sec, RelocationSection):
                continue
            for rel in sec.iter_relocations():
                off, kind = rel['r_offset'] - start, rel['r_info_type']
                if sec['sh_info'] != fn['st_shndx'] or off < 0 or off % 4 or off + 4 > min(len(code), len(want)):
                    fail(off, 'Relocation outside the complete function window or in another section.')
                    continue
                covered.append((off, kind))
                if sec.is_RELA() or kind not in (2, 4, 5, 6, 7):
                    fail(off, 'Unsupported MIPS relocation; a project adapter must prove it.')
                    continue
                sym = elf.get_section(sec['sh_link']).get_symbol(rel['r_info_sym'])
                target = None
                if sym['st_shndx'] == 'SHN_UNDEF':
                    target = self.symbol_address(sym.name, place[0])
                elif sym['st_shndx'] == 'SHN_ABS':
                    target = sym['st_value']
                elif sym['st_shndx'] == fn['st_shndx'] and start <= sym['st_value'] < start + len(code):
                    target = place[2] + sym['st_value'] - start
                if target is None:
                    fail(off, 'Unresolved symbol target: ' + sym.name)
                    continue
                word = struct.unpack_from('<I', code, off)[0]
                if kind == 2:
                    check(off, kind, sym.name, (target + word) & 0xffffffff, 0xffffffff)
                elif kind == 4:
                    address = target + ((word & 0x3ffffff) << 2)
                    if address % 4 or address & 0xf0000000 != (place[2] + off + 4) & 0xf0000000:
                        fail(off, 'R_MIPS_26 target is not representable in this jump region.')
                    else:
                        check(off, kind, sym.name, address >> 2, 0x3ffffff)
                elif kind == 5:
                    highs.setdefault((sec['sh_link'], rel['r_info_sym']), []).append((off, word))
                elif kind == 6:
                    low = signed16(word)
                    check(off, kind, sym.name, target + low, 0xffff)
                    for hi_off, hi_word in highs.pop((sec['sh_link'], rel['r_info_sym']), []):
                        value = target + ((hi_word & 0xffff) << 16) + low
                        check(hi_off, 5, sym.name, (value + 0x8000) >> 16, 0xffff)
                else:
                    # Local/section GPREL16 addends can require the original
                    # object GP from .reginfo. Only externals are supported here.
                    if sym['st_shndx'] != 'SHN_UNDEF':
                        fail(off, 'Local GPREL16 requires a project-specific original-object GP proof.')
                        continue
                    try:
                        delta = target + signed16(word) - self.validated_gp(place[0])
                        if not -32768 <= delta <= 32767:
                            raise ValueError('GP-relative target is outside signed 16-bit range.')
                        check(off, kind, sym.name, delta, 0xffff)
                    except ValueError as exc:
                        fail(off, str(exc))
        for pairs in highs.values():
            for off, _ in pairs:
                fail(off, 'Unpaired R_MIPS_HI16 relocation.')
        return dict(passed=not unresolved and all(r['ok'] for r in checked), checked=checked,
                    unresolved=unresolved, coveredRelocations=covered)

    def compare(self, name, source, with_lines=True, owner=None, timeout=90):
        self.assert_access(name, owner)
        if not isinstance(source, str) or len(source) > 2_000_000:
            raise ValueError('Invalid source draft.')
        import rough
        from local_process import run
        folder = rough.directory(self.root, name, 'compare')
        snapshot = folder / 'candidate.c'
        snapshot.write_text(source, encoding='utf-8', newline='\n')
        obj = snapshot.with_suffix('.o')
        place = self.place(name)
        result = dict(name=name, snapshot=str(snapshot), sourceHash=sha(snapshot), retailSize=place[3],
                      byteMatch=False, rows=[], relocations=None, verdict='Compiler error', flags=[])
        try:
            config.verified_inputs(self.root, self.config)
            # cflags are evidence from historical seeds, never an unchecked CLI.
            flags = re.findall(r'/\*\s*cflags:\s*(.*?)\*/', source, re.S)
            if flags and (len(flags) != 1 or flags[0].split() != self.config.get('sourceFlags', [])):
                raise ValueError('Draft cflags differ from the fixed project/file compiler profile.')
            values = dict(python=sys.executable, root=str(self.root), name=name, program=place[0],
                          source=str(snapshot), object=str(obj))
            command = [arg.format_map(values) for arg in self.config['compileCommand']]
            process = run(command, cwd=self.root, text=True, encoding='utf-8', errors='replace', timeout=timeout)
            result.update(command=command, log=process.stdout + process.stderr, flags=self.config.get('sourceFlags', []))
            (folder / 'compiler.log').write_text(result['log'], encoding='utf-8')
            if process.returncode or not obj.is_file():
                raise ValueError('Compilation failed; see compiler.log.')
            code, masks, start = self.compiled(obj, name)
            want = self.disc_bytes(place)
            refs = self.relocations(obj, code, start, want, place)
            exact = len(code) == len(want) and self.fields(code, masks, start) == self.fields(want, masks, start)
            import rabbitizer
            rows = []
            for off in range(0, max(len(code), len(want)), 4):
                left = struct.unpack_from('<I', code, off)[0] if off + 4 <= len(code) else None
                right = struct.unpack_from('<I', want, off)[0] if off + 4 <= len(want) else None
                mask = masks.get(start + off, 0)
                decode = lambda w: rabbitizer.Instruction(w, place[2] + off, rabbitizer.InstrCategory.R5900).disassemble() if w is not None else '—'
                rows.append(dict(address='%08X' % (place[2] + off), offset=off, ours=decode(left), retail=decode(right),
                    oursHex=code[off:off+4].hex(' ').upper() if left is not None else '—',
                    retailHex=want[off:off+4].hex(' ').upper() if right is not None else '—',
                    mask='%08X' % mask, relocated=bool(mask), equal=left is not None and right is not None and left & ~mask == right & ~mask))
            result.update(verdict='Byte-Match' if exact else 'Code abweichend', byteMatch=exact, compiledSize=len(code),
                          differentWords=sum(not r['equal'] for r in rows), relocations=refs, rows=rows, objectHash=sha(obj))
        except Exception as error:
            result['log'] = result.get('log', '') + '\n' + str(error)
        (folder / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
        return result

    def strict(self, name, result):
        problems, placements = [], []
        obj = Path(result['snapshot']).with_suffix('.o')
        if not obj.is_file() or not result.get('byteMatch'):
            return dict(passed=False, problems=['No exact compiled candidate.'], placements=[])
        if sha(Path(result['snapshot'])) != result.get('sourceHash') or sha(obj) != result.get('objectHash'):
            return dict(passed=False, problems=['Candidate evidence hashes differ.'], placements=[])
        elf = ELFFile(io.BytesIO(obj.read_bytes()))
        st = elf.get_section_by_name('.symtab')
        funcs = [s for s in st.iter_symbols() if s['st_info']['type'] == 'STT_FUNC' and isinstance(s['st_shndx'], int)]
        if len(funcs) != 1 or funcs[0].name != name:
            return dict(passed=False, problems=['Extra or missing function definitions.'], placements=[])
        fn = funcs[0]
        text = elf.get_section(fn['st_shndx'])
        alloc = [i for i, s in enumerate(elf.iter_sections()) if s['sh_flags'] & 2 and s['sh_size']]
        if alloc != [fn['st_shndx']] or fn['st_value'] != 0 or fn['st_size'] != text['sh_size']:
            problems.append('Additional data/code or incomplete function window; ownership review required.')
        if any(s['st_shndx'] != 'SHN_UNDEF' and (s['st_info']['type'] == 'STT_OBJECT' or
               s['st_info']['bind'] == 'STB_GLOBAL' and s.name != name) for s in st.iter_symbols()):
            problems.append('Additional global definitions; data ownership remains unresolved.')
        code, masks, start = self.compiled(obj, name)
        for place in self.all_places(name):
            want = self.disc_bytes(place)
            same = len(code) == len(want) and self.fields(code, masks, start) == self.fields(want, masks, start)
            refs = self.relocations(obj, code, start, want, place)
            placements.append(dict(program=place[0], address=place[2], size=place[3], byteMatch=same,
                                   targets=refs, passed=same and refs['passed']))
        import reference_boundaries
        return reference_boundaries.apply(self, name, dict(passed=bool(placements and not problems and all(p['passed'] for p in placements)),
                    problems=problems, placements=placements,
                    review='Technical match only. Independently verify types, ABI, initial values, semantics, ownership, boundaries and full builds.'))

    def pin_inputs(self, names):
        paths = config.verified_inputs(self.root, self.config)
        paths |= {self.root / 'ps2decomp.json', config.path(self.root, self.config['functions'])}
        paths |= set((self.root / 'include').rglob('*.h')) | set(APP.glob('*.py'))
        paths |= set((APP / 'vendor').rglob('*.py'))
        for sub in ('build/ext/m2c', 'tools/ext/m2c'):
            paths |= set((self.root / sub).rglob('*.py')) | set((self.root / sub).rglob('*.h'))
        for name in names:
            for place in self.all_places(name):
                self.assert_reference(place[0])
                paths.add(self.program_path(place[0]))
        return {str(p.resolve()): dict(sha256=sha(p), size=p.stat().st_size, mtime=p.stat().st_mtime_ns)
                for p in sorted(paths) if p.is_file()}

    def environment(self):
        return hashlib.sha256(json.dumps(self.pin_inputs(list(self.places)), sort_keys=True).encode()).hexdigest()
