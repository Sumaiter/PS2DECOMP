"""RAC3 repository/ABI adapter for the copied RAC4 workbench.

Only paths, catalogue identities, compiler invocation and reference resolution
are game-specific. The editor, queue, budgets, history, generic C mutations,
boundary screens and explicit review handoff are reused from the workbench.
"""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
from types import SimpleNamespace
import uuid

from workbench_core import Engine as ManualEngine
from project_claims import ClaimAPI

FUNCTION = re.compile(r'func_(?:L\d\d_)?[0-9A-F]{8}\Z')
APP = Path(__file__).resolve().parent


class Engine(ManualEngine):
    @staticmethod
    def version_availability(repository):
        root = Path(repository)
        ready = all((root / ('baserom/' + p + '.elf')).is_file() for p in ('frontbin', 'boot_elf', 'i5bootn'))
        return [dict(id='pal', label='PAL · SCES_524.56', available=ready,
                     reason='' if ready else 'RAC3-PAL-Programme fehlen.'),
                dict(id='us', label='NTSC-U · SCUS_973.53', available=False,
                     reason='Die bereitgestellte Disc ist PAL; keine US-Referenzen eingerichtet.')]

    def __init__(self, repository, version='pal'):
        self.root = Path(repository).resolve()
        if version != 'pal' or not (self.root / 'config/inventory.tsv').is_file():
            raise ValueError('Kein eingerichtetes RAC3-PAL-Projekt.')
        self.version = version
        self.program = 'frontbin'
        sys.path.insert(0, str(self.root / 'tools'))
        os.environ.update(UYA_VERSION='pal', UYA_TARGET=self.program,
                         UYA_EE29=str(self.root / 'build/toolchains/ee29'),
                         TMPDIR=str(self.root / 'build/tmp'))
        import inventory
        import level_catalogue
        import levels
        import targets
        from elftools.elf.elffile import ELFFile
        self.ELFFile, self.targets, self.catalogue = ELFFile, targets, level_catalogue.read_catalogue()
        self.places, self.aliases, self.remnants = {}, {}, set()
        core_inventory = inventory.read_inventory()
        main_rows = core_inventory[self.program] + [r for r in core_inventory['boot_elf'] if r[0] == 'core.text']
        for section, addr, size, kind in main_rows:
            name = 'func_%08X' % addr
            self.places[name] = ('main', section, addr, size, kind)
            if kind == 'remnant':
                self.remnants.add(name)
        for section, addr, size, kind in core_inventory['i5bootn']:
            name = 'func_%08X' % addr
            self.places[name] = ('i5bootn', section, addr, size, kind)
            if kind == 'remnant':
                self.remnants.add(name)
        boot_by_name = {'func_%08X' % addr: (section, addr, size) for section, addr, size, _ in core_inventory['boot_elf']}
        self.boot_twins = {}
        for boot_name, frontbin_name in level_catalogue.read_pairs().items():
            if frontbin_name in self.places and boot_name in boot_by_name:
                section, address, size = boot_by_name[boot_name]
                self.boot_twins[frontbin_name] = ('boot_elf', section, address, size)
        for name, kind, size, _, remnant, places in self.catalogue:
            if kind == 'frontbin':
                if name in self.places:
                    self.aliases[name] = [self.places[name][:4]] + [(f'level_{n:02d}', '.text', a, size) for n, a in places]
            elif FUNCTION.fullmatch(name):
                entries = [(f'level_{n:02d}', '.text', a, size) for n, a in places]
                self.places[name] = (*entries[0], 'remnant' if remnant else 'function')
                self.aliases[name] = entries
                if remnant:
                    self.remnants.add(name)
        for name, twin in self.boot_twins.items():
            self.aliases.setdefault(name, [self.places[name][:4]]).append(twin)
        self.levels = SimpleNamespace(read_table=levels.read_table, elf_path=levels.elf_path,
                                      program_path=lambda name: str(self.program_path(name)))
        self._elf = {}
        self._reference_hashes = {}
        self._gp = {}
        self._symbols = {}
        for name in self.places:
            for program, _, address, _ in self.all_places(name):
                self._symbols.setdefault(program, {})[address] = name
        self.owners, self._matched, self.contracts = {}, {}, {}
        self._index_sources()
        self.owner_paths = {name: {path} for name, path in self.owners.items()}
        import srcfiles
        boot_files = srcfiles.read_file_list(str(self.root / self.targets.get('boot_elf').files))
        for name, twin in self.boot_twins.items():
            path = srcfiles.file_for_address(boot_files, twin[2])
            if path:
                self.owner_paths.setdefault(name, set()).add(path)
        self.maps = SimpleNamespace(read_map=lambda: list(self._matched.values()))
        self.check = SimpleNamespace(disc_bytes=self.disc_bytes, places=lambda: self.places)
        self.retail = SimpleNamespace(fields=self.fields, reloc_masks=self.reloc_masks)
        self.try_func = SimpleNamespace(compiled=self.compiled)
        self.layout = SimpleNamespace()
        self.example_cache = None
        self.manual_owner = 'rac3-editor-' + uuid.uuid4().hex

    def _index_sources(self):
        paths = (list((self.root / 'src' / self.program).rglob('*.c'))
                 + list((self.root / 'src/boot_elf/core').rglob('*.c'))
                 + list((self.root / 'src/i5bootn').rglob('*.c'))
                 + list((self.root / 'src/levels').rglob('*.c')))
        definition = re.compile(r'(?m)^([\w \t*]+\b(func_(?:L\d\d_)?[0-9A-F]{8})\s*\([^;{}]*\))\s*\{')
        for path in paths:
            source = path.read_text(encoding='utf-8', errors='replace')
            names = set(re.findall(r'\bfunc_(?:L\d\d_)?[0-9A-F]{8}\b', source))
            owned = set(re.findall(r'localdecomp:start\s+(func_(?:L\d\d_)?[0-9A-F]{8})', source))
            owned.update(re.findall(r'(?:INCLUDE_ASM|ASM_FUNC|LINKER_REMNANT)\([^;]*?,\s*(func_(?:L\d\d_)?[0-9A-F]{8})\s*\)', source))
            definitions = {m[2]: m[1] for m in definition.finditer(source)}
            owned.update(definitions)
            if '/levels/' in path.as_posix():
                owned = {n for n in owned if n.startswith('func_L')}
            for name in owned & self.places.keys():
                self.owners[name] = path.relative_to(self.root).as_posix()
                if not re.search(r'INCLUDE_ASM\([^;]*?,\s*' + name + r'\s*\)', source):
                    self._matched[name] = dict(name=name, file=self.owners[name], status='match', places=self.all_places(name))
                if name in definitions:
                    self.contracts.setdefault(name, []).append(dict(declaration=' '.join(definitions[name].split()) + ';',
                        status='Bestehende RAC3-Quelldeklaration; ABI vor Abschluss prüfen',
                        evidence=hashlib.sha256(path.read_bytes()).hexdigest(), source=str(path), notes=''))

    def program_path(self, program):
        return self.root / self.targets.get(self.program if program == 'main' else program).elf

    def program_target(self, program):
        return self.program if program == 'main' else program

    def reference_path(self, place):
        """Resolve the same split executable context used by disc_bytes."""
        program, section = place[:2]
        if program == 'main' and section == 'core.text':
            program = 'boot_elf'
        return self.program_path(program)

    def reference_identity(self, place):
        program, section = place[:2]
        if program == 'main' and section == 'core.text':
            program = 'boot_elf'
        target = self.targets.get(self.program_target(program))
        return dict(path=self.reference_path(place), algorithm='sha1', digest=target.sha1,
                    eeSections=['.text', 'core.text', 'net.text'],
                    eeSectionsEvidence='PAL EE function inventory and split executable integration; VU sections are separate.')

    def elf(self, program):
        if program not in self._elf:
            self._elf[program] = self.ELFFile(io.BytesIO(self.program_path(program).read_bytes()))
        return self._elf[program]

    def disc_bytes(self, place):
        program, section, address, size = place[:4]
        if program == 'main' and section == 'core.text':
            program = 'boot_elf'
        sec = self.elf(program).get_section_by_name(section)
        if sec is None or not sec['sh_addr'] <= address or address + size > sec['sh_addr'] + sec['sh_size']:
            raise ValueError('Referenzfenster außerhalb des katalogisierten Segments.')
        start = address - sec['sh_addr']
        return sec.data()[start:start + size]

    def assert_reference(self, program):
        if program == 'main':
            self.assert_reference('boot_elf')
        path = self.program_path(program)
        blob = path.read_bytes()
        t = self.targets.get(self.program_target(program))
        elf = self.ELFFile(io.BytesIO(blob))
        if hashlib.sha1(blob).hexdigest() != t.sha1 or elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_MIPS':
            raise ValueError(program + ': RAC3-Referenz stimmt nicht mit dem Katalog überein.')

    def validated_gp(self, program):
        if program not in self._gp:
            import gp
            self.assert_reference(program)
            actual, _ = gp.from_elf(str(self.program_path(program)))
            expected = self.targets.get(self.program_target(program)).gp
            if actual is None or actual != expected:
                raise ValueError(program + ': GP ist nicht durch die RAC3-Disc bestätigt.')
            self._gp[program] = actual
        return self._gp[program]

    def place(self, name):
        if not FUNCTION.fullmatch(name) or name not in self.places:
            raise ValueError('Unbekannte RAC3-Katalogfunktion.')
        return self.places[name]

    def all_places(self, name):
        return self.aliases.get(name, [self.place(name)[:4]])

    def additional_search_children(self, name, source, variants):
        """Offer native-evidenced PAL reconstructions under the shared budgets.

        These are search candidates. Normal byte, relocation, placement and
        independent semantic review gates still decide what can be accepted.
        """
        from . import quadword_zero, symbolic_memory, extern_sizes
        zero_children = quadword_zero.children(self, name, source, variants)
        yield from zero_children
        yield from symbolic_memory.children(self, name, source, variants)
        size_children = extern_sizes.children(self, name, source, variants)
        yield from size_children
        # Both proofs apply to the unchanged root. Offer their composition now:
        # the shared AST parser intentionally cannot parse the helper include
        # or assembly metadata in a later-generation parent. No parser policy,
        # history exclusion or search budget is relaxed for the composition.
        for _, hinted in size_children:
            if not source or not hinted.endswith(source):
                continue
            prefix = hinted[:-len(source)]
            if not re.fullmatch(r'(?:__asm__\("\.extern D_[0-9A-F]{8}(?:_sda)?, 4"\);\n)+', prefix):
                continue
            for label, child in zero_children:
                yield label + '-with-proven-gp-size', prefix + child

    def status(self):
        from workbench_core import BLOCKS
        return dict(repository=str(self.root), python=sys.executable,
                    compiler=(self.root / 'build/toolchains/sn/bin/ee-gcc2953.exe').is_file(),
                    retail=self.program_path('main').is_file(), functions=len(self.places),
                    contracts=len(self.contracts), blocks=BLOCKS)

    def inventory_state(self):
        holders = {}
        api = ClaimAPI(self.root)
        for p in api.DIR.glob('func_*'):
            if FUNCTION.fullmatch(p.name):
                holders[p.name] = api.holder(p.name)
        matched = {p[:3] for row in self._matched.values() for p in row['places']}
        return dict(self._matched), matched, {}, holders

    def assert_access(self, name, owner=None):
        self.place(name)
        holder = ClaimAPI(self.root).holder(name)
        if holder and holder not in (owner, self.manual_owner):
            raise ValueError(name + ': reserviert durch ' + holder + '. Zuerst ausdrücklich an Review freigeben.')

    @contextmanager
    def manual_lease(self, name):
        if name in self._matched:
            yield
            return
        self.assert_access(name)
        from RAC3_PAL import rac3_coordination
        rac3_coordination.assert_free(self, [name])
        api = ClaimAPI(self.root)
        if not api.claim(self.manual_owner, name):
            raise ValueError('Funktion inzwischen reserviert.')
        try:
            yield
        finally:
            api.release(self.manual_owner, name)

    def asm(self, name):
        # Rebuild the listing from actual bytes with EABI-compatible register names.
        import rabbitizer
        place = self.place(name)
        raw = self.disc_bytes(place)
        from . import branch_operands, cop1_branches
        cop1 = cop1_branches.labels(raw, place[2])
        integer = branch_operands.integer_labels(raw, place[2])
        labels = set()
        records = []
        for off in range(0, len(raw), 4):
            address = place[2] + off
            value = struct.unpack_from('<I', raw, off)[0]
            ins = rabbitizer.Instruction(value, address, rabbitizer.InstrCategory.R5900)
            text = ins.disassemble()
            if off in cop1:
                mnemonic, target = cop1[off]
                labels.add(target)
                text = mnemonic + ' .L%08X' % target
            elif off in integer:
                mnemonic, registers, target = integer[off]
                labels.add(target)
                text = mnemonic + ' ' + ', '.join([*('$' + register for register in registers), '.L%08X' % target])
            elif ins.isBranch():
                target = ins.getBranchVramGeneric()
                labels.add(target)
                text = re.sub(r'0x[0-9A-Fa-f]+\s*$', '.L%08X' % target, text)
            elif value >> 26 in (2, 3):
                target = ((address + 4) & 0xf0000000) | ((value & 0x3ffffff) << 2)
                symbol = self._symbols.get(place[0], {}).get(target, 'func_%08X' % target)
                text = text.split()[0] + ' ' + symbol
            records.append((address, value, text))
        out = ['glabel ' + name]
        for address, value, text in records:
            if address in labels:
                out.append('.L%08X:' % address)
            out.append('/* %08X %08X */  %s' % (address, value, text))
        return '\n'.join(out) + '\n'

    def decompile(self, name, owner=None, timeout=45):
        import rough
        self.assert_access(name, owner)
        from . import division_padding
        padding = division_padding.inspect(self, name)
        directory = rough.directory(self.root, name, 'decompile')
        assembly = self.asm(name)
        (directory / 'original.s').write_text(assembly, encoding='utf-8', newline='\n')
        assembly, decoded = rough.normalize_raw_cop1(assembly)
        from . import sqrt_encoding
        assembly, sqrt_decoded = sqrt_encoding.normalize(assembly)
        decoded += sqrt_decoded
        from . import gp_identity
        assembly, gp_identities, gp_diagnostics = gp_identity.normalize_with_diagnostics(self, name, assembly)
        gp = self.validated_gp(self.place(name)[0]) if re.search(r'\$gp\b', assembly) else 0
        if re.search(r'\*/\s*\w+\s+\$gp\s*,', assembly):
            raise ValueError('Die Funktion verändert GP; automatische Auflösung bleibt offen.')
        resolved = [symbol for _, symbol in sorted(
            (offset, identity['symbol']) for identity in gp_identities for offset in identity['offsets'])]
        def memory(m):
            symbol = 'D_%08X' % ((gp + int(m[2], 0)) & 0xffffffff)
            # Shared RAC3 code names GP slots, not one multiplayer address.
            if name.startswith('func_L'):
                import gp as gp_tools
                symbol = 'D_%08X_sda' % ((gp_tools.STORY_GP + int(m[2], 0)) & 0xffffffff)
            resolved.append(symbol)
            return m[1] + '%gp_rel(' + symbol + ')($gp)'
        assembly = re.sub(r'(\$\w+,\s*)(-?(?:0x[0-9A-Fa-f]+|\d+))\(\$gp\)', memory, assembly)
        def address_from_gp(m):
            symbol = 'D_%08X' % ((gp + int(m[2], 0)) & 0xffffffff)
            if name.startswith('func_L'):
                import gp as gp_tools
                symbol = 'D_%08X_sda' % ((gp_tools.STORY_GP + int(m[2], 0)) & 0xffffffff)
            resolved.append(symbol)
            return m[1] + '%gp_rel(' + symbol + ')'
        assembly = re.sub(r'((?:addiu|daddiu)\s+\$\w+,\s*\$gp,\s*)(-?(?:0x[0-9A-Fa-f]+|\d+))', address_from_gp, assembly)
        assembly, register_aliases = rough.normalize_ee_registers(assembly)
        (directory / 'input.s').write_text(assembly, encoding='utf-8', newline='\n')
        callees = re.findall(r'\bfunc_(?:L\d\d_)?[0-9A-F]{8}\b', assembly)
        declarations, skipped = rough.context(self.contracts, [name, *callees])
        (directory / 'context.c').write_text(rough.TYPES + '\n'.join(declarations) + '\n', encoding='utf-8', newline='\n')
        command = [sys.executable, str(rough.tool(self.root)), '-t', 'mipsee-gcc-c', '--valid-syntax', '--no-cache',
                   '--context', str(directory / 'context.c'), '-f', name, str(directory / 'input.s')]
        from local_process import run as execute
        run = execute(command, cwd=directory, text=True, encoding='utf-8', errors='replace', timeout=timeout)
        (directory / 'm2c-output.c').write_text(run.stdout, encoding='utf-8', newline='\n')
        (directory / 'm2c.log').write_text(run.stderr, encoding='utf-8', newline='\n')
        ok = run.returncode == 0 and bool(re.search(r'\b' + name + r'\s*\([^;]*\)\s*\{', run.stdout))
        source = '#include "common.h"\n\n/* Unverified automatic m2c draft; review types, ABI and ownership. */\n' + '\n'.join(declarations) + '\n' + run.stdout if ok else ''
        markers = sorted(set(re.findall(r'\b(?:M2C_\w+|saved_reg_\w+)\b', run.stdout)))
        abi_problems = ['Nicht dekodierte Originalinstruktion bei ' + address
                        for address in re.findall(r'(?m)^/\* ([0-9A-F]+) [0-9A-F]+ \*/\s*\.word\b', assembly)]
        definition = re.search(r'\b' + name + r'\s*\(([^()]*)\)\s*\{', run.stdout)
        if definition and not any(re.search(r'\b' + name + r'\s*\(', d) for d in declarations):
            for index, parameter in enumerate(definition[1].split(',')):
                slot = re.search(r'\barg(\d+)\s*$', parameter.strip())
                if slot and int(slot[1]) != index:
                    abi_problems.append('m2c hat unbenutzte Argumentpositionen ausgelassen; vollständige Signatur belegen.')
                    break
        try:
            forwarded = rough.forwarding_evidence(self, name)
        except Exception as exc:
            forwarded = dict(findings=[], warnings=[str(exc)])
        from . import context_inputs
        context_evidence = context_inputs.inspect(self, declarations)
        from .gp_diagnostics import warnings as gp_warnings
        result = dict(name=name, ok=ok, source=source, warnings=[*skipped, *division_padding.warnings(padding), *context_inputs.warnings(context_evidence), *gp_warnings(gp_diagnostics), 'RAC3-Grobentwurf; Typen, ABI, Datenbesitz und Funktionsgrenzen prüfen.'],
                      log=run.stderr, directory=str(directory), command=command, adapterVersion=rough.ADAPTER_VERSION,
                      contextDeclarations=declarations, abiProblems=abi_problems, gpResolutions=resolved, gpIdentities=gp_identities, instructionDecodings=decoded,
                      registerAliases=register_aliases, unresolvedMarkers=markers, forwardedArguments=forwarded,
                      divisionPadding=padding, contextInputs=context_evidence, gpIdentityDiagnostics=gp_diagnostics)
        (directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
        return result

    def _compare_place(self, name, source, place, folder, timeout, obj=None):
        folder.mkdir(parents=True, exist_ok=True)
        snippet = folder / 'candidate.c'
        snippet.write_text(source, encoding='utf-8', newline='\n')
        target = 'boot_elf' if place[0] == 'main' and place[1] == 'core.text' else self.program_target(place[0])
        command = [sys.executable, str(APP / 'check_worker.py'), '--repo', str(self.root), '--target', target,
                   '--name', name, '--source', str(snippet), '--out', str(folder)]
        if obj:
            command += ['--object', str(obj), '--address', str(place[2]), '--size', str(place[3])]
            if place[0] == 'boot_elf' and name in self.boot_twins:
                command += ['--frontbin-copy']
        from local_process import run as execute
        run = execute(command, cwd=self.root, text=True, encoding='utf-8', errors='replace', timeout=timeout)
        try:
            result = json.loads(run.stdout)
        except ValueError:
            result = dict(ok=False, message=run.stdout + run.stderr)
        return result

    def compare(self, name, source, with_lines=True, owner=None, timeout=90):
        self.assert_access(name, owner)
        folder = self.root / 'build/studio' / name / uuid.uuid4().hex
        place = self.place(name)
        snapshot = folder / 'candidate.c'
        base = dict(name=name, snapshot=str(snapshot), sourceHash=hashlib.sha256(source.encode('utf-8')).hexdigest(),
                    retailSize=place[3], review='Technischer Vergleich; vollständige unabhängige Review erforderlich.')
        try:
            meta = self._compare_place(name, source, place, folder, timeout)
        except subprocess.TimeoutExpired:
            meta = dict(ok=False, message='Compiler-Zeitlimit erreicht.')
        base['log'] = meta.get('log', meta.get('message', ''))
        if not meta.get('ok'):
            base.update(verdict='Compilerfehler', byteMatch=False, relocations=None, rows=[])
        else:
            shutil.copy2(folder / 'candidate.o', snapshot.with_suffix('.o')) if snapshot.with_suffix('.o') != folder / 'candidate.o' else None
            ours = (folder / 'source.bin').read_bytes()
            theirs = (folder / 'target.bin').read_bytes()
            import rabbitizer
            rows = []
            for off in range(0, max(len(ours), len(theirs)), 4):
                left = struct.unpack_from('<I', ours, off)[0] if off + 4 <= len(ours) else None
                right = struct.unpack_from('<I', theirs, off)[0] if off + 4 <= len(theirs) else None
                decode = lambda word: rabbitizer.Instruction(word, 0, rabbitizer.InstrCategory.R5900).disassemble() if word is not None else '—'
                rows.append(dict(address='%08X' % (place[2] + off), offset=off, ours=decode(left), retail=decode(right), equal=left is not None and left == right,
                    relocated=False, oursHex=ours[off:off + 4].hex(' ').upper() if left is not None else '—',
                    retailHex=theirs[off:off + 4].hex(' ').upper() if right is not None else '—', mask='00000000'))
            checked = meta.get('relocations', [])
            relocs = dict(passed=not meta['unresolvedRelocations'] and all(r.get('ok') is True for r in checked), checked=checked,
                          unresolved=[dict(offset=int(off, 16), reason='RAC3-Symbolziel nicht aufgelöst.') for off in meta['unresolvedRelocations']],
                          coveredRelocations=meta.get('coveredRelocations', []))
            base.update(verdict='Byte-Match' if meta['technicalExact'] else 'Code abweichend', byteMatch=meta['technicalExact'],
                        compiledSize=meta['compiledSize'], differentWords=sum(not r['equal'] for r in rows), flags=meta['flags'],
                        relocations=relocs, rows=rows, rac3Evidence=meta)
        (folder / 'result.json').write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
        return base

    def strict(self, name, result):
        evidence = result.get('rac3Evidence', {})
        if not evidence and result.get('snapshot'):
            path = Path(result['snapshot']).with_name('evidence.json')
            if path.is_file():
                evidence = json.loads(path.read_text(encoding='utf-8'))
        problems = []
        if evidence.get('dataSections'):
            problems.append('Zusätzliche Datensektionen; Datenbesitz offen.')
        if evidence.get('extraDefinitions'):
            problems.append('Zusätzliche globale Definitionen.')
        placements = []
        if result.get('byteMatch'):
            primary = self.place(name)[:4]
            source = Path(result['snapshot']).read_text(encoding='utf-8')
            for place in self.all_places(name):
                self.assert_reference(place[0])
                meta = evidence if tuple(place) == tuple(primary) else self._compare_place(name, source, place,
                    Path(result['snapshot']).parent / ('placement-' + place[0]), 90,
                    obj=Path(result['snapshot']).with_suffix('.o'))
                passed = bool(meta.get('ok') and meta.get('technicalExact') and not meta.get('dataSections') and not meta.get('extraDefinitions'))
                placements.append(dict(program=place[0], address=place[2], size=place[3], passed=passed,
                                       byteMatch=meta.get('technicalExact', False), targets=dict(passed=passed)))
        import reference_boundaries
        return reference_boundaries.apply(self, name, dict(passed=bool(placements and not problems and all(p['passed'] for p in placements)),
                    problems=problems, placements=placements, review='Technischer Treffer; Typ-/ABI-/Semantikreview und vollständige Builds bleiben erforderlich.'))

    def symbol_address(self, symbol, program='main'):
        if program in self._symbols:
            for address, name in self._symbols[program].items():
                if name == symbol:
                    return address
        m = re.fullmatch(r'D_([0-9A-F]{8})(_sda)?(?:_\w+)?', symbol)
        if m:
            value = int(m[1], 16)
            return value + self.validated_gp(program) - 0x1dc8b0 if m[2] else value
        m = re.fullmatch(r'func_([0-9A-F]{8})', symbol)
        return int(m[1], 16) if m else None

    @staticmethod
    def fields(code, masks, start):
        return bytes(code)

    @staticmethod
    def reloc_masks(elf, index):
        return {}

    def compiled(self, obj, name):
        evidence = Path(obj).with_name('evidence.json')
        resolved = Path(obj).with_name('source.bin')
        if evidence.is_file() and resolved.is_file():
            data = json.loads(evidence.read_text(encoding='utf-8'))
            if (data.get('name') == name and data.get('objectHash') == hashlib.sha256(Path(obj).read_bytes()).hexdigest()
                    and data.get('resolvedBodyHash') == hashlib.sha256(resolved.read_bytes()).hexdigest()):
                return resolved.read_bytes(), {}, 0
        with Path(obj).open('rb') as stream:
            elf = self.ELFFile(stream)
            sym = next((s for s in elf.get_section_by_name('.symtab').iter_symbols() if s.name == name), None)
            if sym is None:
                return None
            sec = elf.get_section(sym['st_shndx'])
            return sec.data()[sym['st_value']:sym['st_value'] + sym['st_size']], {}, sym['st_value']

    def relocations(self, obj, code, start, want, place):
        evidence = Path(obj).with_name('evidence.json')
        if not evidence.is_file():
            return dict(passed=False, checked=[], unresolved=[dict(reason='RAC3-Belege fehlen.')], coveredRelocations=[])
        meta = json.loads(evidence.read_text(encoding='utf-8'))
        passed = meta.get('technicalExact') and self.program_target(place[0]) == meta.get('target')
        return dict(passed=bool(passed), checked=meta.get('relocations', []),
                    unresolved=[] if passed else [dict(reason='Platzierung benötigt eigenen RAC3-Relokationsbeleg.')],
                    coveredRelocations=meta.get('coveredRelocations', []))

    def pin_inputs(self, names):
        paths = set((self.root / 'tools').glob('*.py')) | set((self.root / 'include').rglob('*.h'))
        paths |= set((self.root / 'build/toolchains').rglob('*.exe'))
        paths |= set((self.root / 'build/toolchains').rglob('specs'))
        paths |= set((self.root / 'config').rglob('*.tsv')) | set((self.root / 'config').rglob('text_parts.txt'))
        paths |= set((self.root / 'config').rglob('*.txt'))
        paths |= set((self.root / 'src').rglob('*.c'))
        paths |= set(APP.glob('*.py'))
        paths |= set((self.root / 'build/ext/m2c').rglob('*.py'))
        for name in names:
            for place in self.all_places(name):
                self.assert_reference(place[0])
                paths.add(self.program_path(place[0]))
                if place[0] == 'main':
                    paths.add(self.program_path('boot_elf'))
        return {str(p.resolve()): dict(sha256=hashlib.sha256(p.read_bytes()).hexdigest(), size=p.stat().st_size, mtime=p.stat().st_mtime_ns)
                for p in sorted(paths) if p.is_file()}

    def environment(self):
        paths = [APP / 'rac3_engine.py', APP / 'check_worker.py', self.root / 'include/common.h',
                 self.root / 'tools/check.py', self.root / 'tools/asm_filter.py']
        paths += list((self.root / 'build/toolchains').rglob('*.exe'))
        return hashlib.sha256(''.join(hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)).encode()).hexdigest()
