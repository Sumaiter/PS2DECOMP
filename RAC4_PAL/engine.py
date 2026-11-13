"""PAL automatic-search adapter, including catalogue overlays and strict review gates."""
from __future__ import annotations
from pathlib import Path
import hashlib
import io
import re
import struct
import sys
import locale
from RAC4_PAL.manual_engine import Engine
from search_rules import code_only
import library_contracts

FUNCTION = re.compile(r'func_(?:L\d\d_)?[0-9A-F]{8}\Z')
SOURCE_DEFINITION = re.compile(r'(?m)^([\w \t*]+\b([A-Za-z_]\w*)\s*\([^;{}]*\))\s*\{')


def complete_cfg(raw, start):
    """Reject fragments, tail exits, traps and control instructions in delay slots."""
    if not raw or len(raw) % 4:
        return False
    words = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
    pending, seen, returns = [0], set(), 0
    def control(w):
        op, rs, fn = w >> 26, w >> 21 & 31, w & 63
        return op in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23) or op == 0 and fn in (8, 9, 12, 13, 48, 49, 50, 51, 52, 54) or op == 16 or op in (17, 18) and rs == 8
    def enqueue(offset):
        if offset % 4 or not 0 <= offset < len(raw):
            raise ValueError('CFG außerhalb der Funktion')
        if offset not in seen:
            pending.append(offset)
    try:
        while pending:
            pc = pending.pop()
            if pc in seen:
                continue
            seen.add(pc); w = words[pc // 4]
            op, rs, rt, fn = w >> 26, w >> 21 & 31, w >> 16 & 31, w & 63
            if op in (2, 16) or op == 0 and fn in (12, 13, 48, 49, 50, 51, 52, 54):
                return False
            if op == 1 and rt not in (0, 1, 2, 3):
                return False
            branch = op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op in (17, 18) and rs == 8
            call = op == 3 or op == 0 and fn == 9
            ret = op == 0 and fn == 8
            if branch or call or ret:
                if pc + 4 >= len(raw) or control(words[pc // 4 + 1]):
                    return False
                seen.add(pc + 4)
            if ret:
                if rs != 31:
                    return False
                returns += 1
            elif call:
                enqueue(pc + 8)
            elif branch:
                imm = (w & 0x7fff) - (w & 0x8000)
                known = None
                if op in (4, 20) and rs == rt or op in (6, 22) and rs == 0 or op == 1 and rs == 0 and rt in (1, 3):
                    known = True
                if op in (5, 21) and rs == rt or op in (7, 23) and rs == 0 or op == 1 and rs == 0 and rt in (0, 2):
                    known = False
                if known is not False:
                    enqueue(pc + 4 + imm * 4)
                if known is not True:
                    enqueue(pc + 8)
            else:
                enqueue(pc + 4)
        return bool(returns) and not any(w and i * 4 not in seen for i, w in enumerate(words))
    except ValueError:
        return False


class AutoEngine(Engine):
    def additional_search_children(self, name, source, variants):
        """PAL-only native ordering evidence, after a positive C shape."""
        from .native_local_after_call_wrapper import generate_for_engine
        return generate_for_engine(self, name, source, rules=variants)

    program = "main"

    @staticmethod
    def version_availability(repository):
        root = Path(repository)
        missing = [str(root / p) for p in ('baserom/us/main.elf', 'config/us/functions.tsv', 'config/us/programs.tsv')
                   if not (root / p).is_file()]
        pal_missing = [p for p in ('baserom/pal/main.elf', 'config/pal/functions.tsv', 'config/pal/programs.tsv')
                       if not (root / p).is_file()]
        return [dict(id='pal', label='PAL · SCES_532.85', available=not pal_missing,
                     reason='PAL-Eingaben fehlen: ' + ', '.join(pal_missing) if pal_missing else ''),
                dict(id='us', label='NTSC-U · SCUS_974.65', available=False,
                     reason=('NTSC-U-Eingaben fehlen: ' + ', '.join(missing) + '. ' if missing else '') +
                     'NTSC-U-GP-, Compiler- und Referenzadapter noch nicht vollständig geprüft; kein Suchstart möglich.')]

    def __init__(self, repository, version='pal'):
        if version not in ('pal', 'us'):
            raise ValueError('Unbekannte Spielversion.')
        if version != 'pal':
            raise ValueError(self.version_availability(repository)[1]['reason'])
        self.version = version
        super().__init__(repository)
        import level_catalogue
        catalogue = list(level_catalogue.read_catalogue())
        self.places = {n: p for n, p in self.check.places().items() if FUNCTION.fullmatch(n)}
        self.aliases = {n: [(f'level_{lv:02d}', '.text', addr, size) for lv, addr in places]
                        for n, kind, size, fp, rem, places in catalogue if kind in ('shared', 'level') and not int(rem)}
        # Main functions can also have catalogue-proven overlay copies. They
        # require the primary executable AND every overlay, not just main.
        for n,kind,size,fp,rem,places in catalogue:
            if kind=='main' and not int(rem) and n in self.places and places:
                self.aliases[n]=[self.places[n][:4],
                    *[(f'level_{lv:02d}','.text',addr,size) for lv,addr in places]]
        self.remnants = {n for n, kind, size, fp, rem, places in catalogue if int(rem)}
        self._symbols = {}
        for n, p in self.places.items():
            for program, section, address, size in self.all_places(n):
                self._symbols.setdefault(program, {})[address] = n
        self._add_current_contracts()
        self.library_contracts = library_contracts.add_contracts(self)

    def asm(self, name):
        place = self.place(name)[:4]
        import asm_programs
        raw = self.check.disc_bytes(place)
        # Fresh immutable input from the actual bytes, including missing overlay listings.
        parts, _ = asm_programs.code_section(place[2], raw, {place[2]: name}, set(), self._symbols[place[0]])
        return f'/* {place[0]} {place[1]} 0x{place[2]:08X} */\n\n' + parts[0][2]

    def _add_current_contracts(self):
        # The PAL map proves symbol identity; a src/core or lib path can also be
        # explicitly mapped in PAL. A filename prefix is not a version check.
        # Cache only within this fresh engine construction. Aliased map rows
        # can refer to one file; read/hash it once, with no cross-job staleness.
        sources = {}
        for row in self.maps.read_map():
            if row['status'] != 'match':
                continue
            p = self.root / row['file']
            if p not in sources:
                if not p.is_file():
                    sources[p] = ({}, None)
                else:
                    blob = p.read_bytes()
                    try:
                        text = blob.decode('utf-8-sig')
                    except UnicodeDecodeError:
                        text = blob.decode(locale.getencoding())
                    declarations = {}
                    for match in SOURCE_DEFINITION.finditer(code_only(text)):
                        declarations.setdefault(match[2], ' '.join(match[1].split()) + ';')
                    sources[p] = (declarations, hashlib.sha256(blob).hexdigest())
            declarations, digest = sources[p]
            declaration = declarations.get(row['name'])
            if declaration:
                # Map names are native catalogue identities. A US source name
                # can exist at a different PAL address and must not inherit the
                # source prototype merely because its spelling is unchanged.
                targets = row.get('names', [row['name']])
                for target in dict.fromkeys(targets):
                    alias = target != row['name']
                    if alias:
                        if not FUNCTION.fullmatch(target) or target not in self.places:
                            continue
                        placements = set(self.all_places(target))
                        if not placements or not placements <= set(row.get('places', [])):
                            continue
                    native = re.sub(r'\b' + re.escape(row['name']) + r'(?=\s*\()', target, declaration, count=1)
                    records = self.contracts.setdefault(target, [])
                    if not any(r['declaration'] == native for r in records):
                        records.append({'declaration': native, 'status': 'Bestehende PAL-Matchquelle; Vertrag vor Abschluss prüfen',
                                        'source': str(p), 'evidence': digest,
                                        'notes': 'Expliziter Katalogalias; alle Platzierungen von der Matchzeile abgedeckt.' if alias else '',
                                        'sourceFunction': row['name'], 'mappedAlias': alias})

    def place(self, name):
        if not FUNCTION.fullmatch(name) or name not in self.places:
            raise ValueError('Unbekannte PAL-Katalogfunktion.')
        return self.places[name]

    def all_places(self, name):
        return self.aliases.get(name, [self.place(name)[:4]])

    def assert_reference(self, program):
        identity = self.reference_identity((program,))
        path, expected, entry = identity['path'], identity['digest'], identity['entry']
        blob = path.read_bytes()
        elf = self.ELFFile(io.BytesIO(blob))
        if (hashlib.sha1(blob).hexdigest() != expected or elf.elfclass != 32 or not elf.little_endian
                or elf['e_machine'] != 'EM_MIPS' or elf['e_type'] != 'ET_EXEC' or elf['e_entry'] != entry):
            raise ValueError(f'{program}: Referenz stimmt nicht mit dem PAL-Katalog überein.')

    def reference_identity(self, place):
        program = place[0]
        if program == 'main':
            row = next(line.split('\t') for line in (self.root / 'config/pal/programs.tsv').read_text().splitlines() if line.startswith('main\t'))
            path, expected, entry = self.root / row[1], row[2], int(row[4], 16)
        else:
            row = next(r for r in self.levels.read_table() if r['number'] == int(program[6:]))
            path, expected, entry = Path(self.levels.elf_path(row['number'])), row['elf_sha1'], row['entry']
        return dict(path=path, algorithm='sha1', digest=expected, entry=entry,
                    eeSections=['.text', 'core.text', 'net.text'],
                    eeSectionsEvidence='PAL inventory/check_func EE sections; levels.CODE_SECTIONS also includes non-EE VU code.')

    def symbol_address(self, symbol, program='main'):
        if program.startswith('level_') and symbol.startswith('func_'):
            places = [p for p in self.aliases.get(symbol, []) if p[0] == program]
            return places[0][2] if len(places) == 1 else None
        return super().symbol_address(symbol)

    def strict(self, name, result):
        from elftools.elf.relocation import RelocationSection
        problems = []
        path = Path(result['snapshot']).with_suffix('.o')
        if not path.exists():
            return {'passed': False, 'problems': ['Kein Compilerobjekt.'], 'placements': []}
        elf = self.ELFFile(io.BytesIO(path.read_bytes()))
        st = elf.get_section_by_name('.symtab')
        funcs = [s for s in st.iter_symbols() if s['st_info']['type'] == 'STT_FUNC' and isinstance(s['st_shndx'], int)]
        if len(funcs) != 1 or funcs[0].name != name:
            return {'passed': False, 'problems': ['Zusätzliche oder fehlende Funktionsdefinitionen.'], 'placements': []}
        fn = funcs[0]; text = elf.get_section(fn['st_shndx']); code = text.data()
        alloc = [i for i, s in enumerate(elf.iter_sections()) if s['sh_flags'] & 2 and s['sh_size']]
        if alloc != [fn['st_shndx']] or fn['st_value'] != 0 or fn['st_size'] != len(code):
            problems.append('Zusätzliche Daten/Code oder unvollständiges Funktionsfenster: Review erforderlich.')
        if any(s['st_shndx'] != 'SHN_UNDEF' and (s['st_info']['type'] == 'STT_OBJECT' or
               s['st_info']['bind'] == 'STB_GLOBAL' and s.name != name) for s in st.iter_symbols()):
            problems.append('Zusätzliche globale Definitionen: Datenbesitz offen.')
        rels = []
        for section in elf.iter_sections():
            if not isinstance(section, RelocationSection):
                continue
            for r in section.iter_relocations():
                if section['sh_info'] != fn['st_shndx'] or r['r_info_type'] not in (4, 5, 6, 7):
                    problems.append('Nicht unterstützte oder nicht abgedeckte Relokation.')
                rels.append((r['r_offset'], r['r_info_type']))
        checked = result.get('relocations') or {}
        if sorted(rels) != sorted(tuple(r) for r in checked.get('coveredRelocations', [])):
            problems.append('Nicht alle Relokationen wurden auf tatsächliche Ziele geprüft.')
        placements = []
        if result.get('byteMatch'):
            # Do not let the primary-placement relocation mask stand for all clones.
            for place in self.all_places(name):
                want = self.check.disc_bytes(place)
                masks = self.retail.reloc_masks(elf, fn['st_shndx'])
                same = len(code) == len(want) and self.retail.fields(code, masks, 0) == self.retail.fields(want, masks, 0)
                refs = self.relocations(path, code, 0, want, place) if same else {'passed': False}
                placements.append({'program': place[0], 'address': place[2], 'size': place[3],
                                   'byteMatch': same, 'targets': refs, 'passed': same and refs['passed']})
        passed = bool(result.get('byteMatch') and checked.get('passed') and placements and not problems
                      and all(p['passed'] for p in placements))
        import reference_boundaries
        return reference_boundaries.apply(self, name, {'passed': passed, 'problems': problems, 'placements': placements,
                'review': 'Technischer Treffer. Signaturen, Initialwerte und Semantik benötigen Review.'})


def similarity(result):
    """Instruction edit similarity is a search score, never a correctness percentage."""
    rows = result.get('rows', [])
    left = [(r['oursHex'], r['ours'].split()[0], int(r['mask'], 16)) for r in rows if r['oursHex'] != '—']
    right = [(r['retailHex'], r['retail'].split()[0], int(r['mask'], 16)) for r in rows if r['retailHex'] != '—']
    def token(item):
        raw, op, mask = item
        return int.from_bytes(bytes.fromhex(raw), 'little') & ~mask, op
    a, b = list(map(token, left)), list(map(token, right))
    if not a or not b:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, (value, op) in enumerate(a, 1):
        cur = [float(i)]
        for j, (other, other_op) in enumerate(b, 1):
            cost = 0 if value == other else .35 if op == other_op else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return round(max(0, 100 * (1 - prev[-1] / max(len(a), len(b)))), 3)
