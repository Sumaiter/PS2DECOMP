"""Synthetic ELF counterexamples for an adapter-owned, metadata-only search rule."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import io
import json
import struct
import tempfile
import unittest

from elftools.elf.elffile import ELFFile
from tests.elf_fixture import elf
from search_rules import Variants
from RAC3_PAL import extern_sizes


class ExternSizeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.name = 'func_L01_00100000'
        self.symbol = 'D_001DC8F0'
        self.owner = 'src/levels/synthetic/source.c'
        self.flags = ['-O2', '-G8', '-fopt-stack', '-mno-check-zero-division', '@ps2as']
        self.source = ('#include "common.h"\nextern f32 ' + self.symbol + ';\n'
                       'f32 ' + self.name + '(void) { return ' + self.symbol + '; }\n')
        self.places = [('level_01', '.text', 0x100000, 12)]
        self.raw = {'level_01': struct.pack('<III', 0xC7800040, 0x03E00008, 0)}
        self.gps = {'level_01': 0x1DC8B0}
        self.put('include/common.h', '#ifndef COMMON_H\n#define COMMON_H\ntypedef float f32;\n#endif\n')
        self.put(self.owner, '#include "common.h"\n/* synthetic owner context */\n')
        self.put('config/levels/text_parts.txt', self.owner + '\t' + ' '.join(self.flags) + '\n')
        self.put('tools/check.py', '# Synthetic checker identity, never executed.\n')
        self.put('build/toolchains/sn/bin/compiler.exe', b'synthetic-not-executable')
        self.reference_calls = []
        self.engine = SimpleNamespace(root=self.root, version='pal', owners={self.name: self.owner},
            owner_paths={self.name: {self.owner}}, place=lambda _: self.places[0],
            all_places=lambda _: list(self.places), assert_reference=self.assert_reference,
            validated_gp=lambda program: self.gps[program],
            program_path=lambda program: self.root / 'baserom' / (program + '.elf'),
            check=SimpleNamespace(disc_bytes=lambda place: self.raw[place[0]]))
        self.folder = self.root / 'build/studio' / self.name / 'immutable'
        self.log = self.root / 'build/autodecomp/synthetic-session/jobs' / self.name / 'attempts.jsonl'
        self.session = self.log.parents[2] / 'session.json'
        self.install_evidence()

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(value, encoding='utf-8', newline='\n')
        return path

    def save(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8', newline='\n')

    def assert_reference(self, program):
        self.reference_calls.append(program)
        if self.engine.program_path(program).read_bytes() != b'synthetic-reference-' + program.encode():
            raise ValueError('Changed reference identity')

    def install_evidence(self, *, width=4):
        for place in self.places:
            self.put('baserom/' + place[0] + '.elf', b'synthetic-reference-' + place[0].encode())
        self.folder.mkdir(parents=True, exist_ok=True)
        snapshot = self.folder / 'candidate.c'
        snapshot.write_text(self.source, encoding='utf-8', newline='\n')
        words = [0x3C010000, 0xC4200000, 0x03E00008, 0]
        blob = bytearray(elf(words, name=self.name, external=self.symbol,
                             relocations=((0, 5), (4, 6))))
        parsed = ELFFile(io.BytesIO(blob))
        # The authored fixture's external symbol gets a compiler-proven size.
        symtab = parsed.get_section_by_name('.symtab')
        struct.pack_into('<I', blob, symtab['sh_offset'] + 2 * 16 + 8, width)
        (self.folder / 'candidate.o').write_bytes(blob)
        body = struct.pack('<IIII', 0x3C01001E, 0xC420C8F0, 0x03E00008, 0)
        (self.folder / 'source.bin').write_bytes(body)
        (self.folder / 'target.bin').write_bytes(self.raw[self.places[0][0]])
        self.result = dict(name=self.name, snapshot=str(snapshot), sourceHash=extern_sizes._sha(self.source.encode()),
            rac3Evidence=dict(objectHash=extern_sizes._sha(blob), flags=self.flags,
                address=self.places[0][2], retailSize=self.places[0][3], target=self.places[0][0],
                compiledSize=len(body), unresolvedRelocations=[], dataSections=[], extraDefinitions=[],
                resolvedBodyHash=extern_sizes._sha(body)))
        self.save(self.folder / 'result.json', self.result)
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.write_text(json.dumps(dict(snapshot=str(snapshot), sourceHash=self.result['sourceHash'],
            objectHash=self.result['rac3Evidence']['objectHash'])) + '\n', encoding='utf-8', newline='\n')
        pins = {str(path): dict(sha256=digest) for path, digest in
                extern_sizes._inputs(self.engine, self.name, self.places).items()}
        worker = Path(extern_sizes.__file__).with_name('check_worker.py')
        pins[str(worker)] = dict(sha256=extern_sizes._sha(worker.read_bytes()))
        self.save(self.session, dict(version='pal', pins=pins))

    def children(self):
        return extern_sizes.children(self.engine, self.name, self.source, Variants(self.root))

    def test_compiler_size_and_native_gp_target_offer_only_early_metadata(self):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        children = self.children()
        self.assertEqual(children, [(extern_sizes.LABEL, '__asm__(".extern ' + self.symbol + ', 4");\n' + self.source)])
        self.assertEqual(self.reference_calls, ['level_01'])
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})

    def test_two_gp_values_are_valid_only_with_a_slot_symbol(self):
        self.symbol += '_sda'
        self.source = self.source.replace('D_001DC8F0', self.symbol)
        self.places.append(('level_40', '.text', 0x200000, 12))
        self.raw['level_40'] = self.raw['level_01']
        self.gps['level_40'] = 0x247FC0
        self.install_evidence()
        self.assertEqual(len(self.children()), 1)
        self.assertEqual(self.reference_calls, ['level_01', 'level_40'])
        self.source = self.source.replace('_sda', '')
        self.symbol = 'D_001DC8F0'
        self.install_evidence()
        self.assertEqual(self.children(), [])

    def test_wrong_target_at_one_placement_rejects_the_rule(self):
        self.places.append(('level_02', '.text', 0x200000, 12))
        self.raw['level_02'] = struct.pack('<III', 0xC7800044, 0x03E00008, 0)
        self.gps['level_02'] = self.gps['level_01']
        self.install_evidence()
        self.assertEqual(self.children(), [])

    def test_unknown_or_different_compiler_size_never_guesses_four(self):
        for width in (0, 8, 16):
            with self.subTest(width=width):
                self.install_evidence(width=width)
                self.assertEqual(self.children(), [])

    def test_two_current_objects_with_conflicting_sizes_are_ambiguous(self):
        original = self.folder
        self.folder = original.with_name('other-immutable')
        self.install_evidence(width=8)
        self.log.write_text(self.log.read_text() + json.dumps(dict(snapshot=str(original / 'candidate.c'),
            sourceHash=self.result['sourceHash'], objectHash=extern_sizes._sha((original / 'candidate.o').read_bytes()))) + '\n',
            encoding='utf-8', newline='\n')
        self.assertEqual(self.children(), [])

    def test_source_object_and_resolved_body_corruption_cannot_supply_metadata(self):
        for filename in ('candidate.c', 'candidate.o', 'source.bin'):
            with self.subTest(filename=filename):
                self.install_evidence()
                p = self.folder / filename
                p.write_bytes(p.read_bytes() + b' ')
                self.assertEqual(self.children(), [])

    def test_changed_header_owner_profile_compiler_or_checker_invalidates_pins(self):
        for relative in ('include/common.h', self.owner, 'config/levels/text_parts.txt',
                         'build/toolchains/sn/bin/compiler.exe', 'tools/check.py'):
            with self.subTest(relative=relative):
                self.install_evidence()
                p = self.root / relative
                old = p.read_bytes()
                p.write_bytes(old + b'\n')
                self.assertEqual(self.children(), [])
                p.write_bytes(old)

    def test_missing_session_pin_reference_identity_or_wrong_version_fails_closed(self):
        self.session.unlink()
        self.assertEqual(self.children(), [])
        self.install_evidence()
        self.engine.program_path('level_01').write_bytes(b'changed reference')
        self.assertEqual(self.children(), [])
        self.install_evidence()
        self.engine.version = 'us'
        self.assertEqual(self.children(), [])

    def test_changed_checker_identity_is_not_reused_as_a_current_type_proof(self):
        data = json.loads(self.session.read_text(encoding='utf-8'))
        worker = Path(extern_sizes.__file__).with_name('check_worker.py')
        data['pins'][str(worker)]['sha256'] = '0' * 64
        self.save(self.session, data)
        self.assertEqual(self.children(), [])

    def test_unresolved_literal_pool_or_extra_definitions_are_not_repaired(self):
        for field, value in [('unresolvedRelocations', ['0x4']), ('dataSections', [{'name': '.lit4', 'size': 4}]),
                             ('extraDefinitions', ['unrelated_function'])]:
            with self.subTest(field=field):
                self.install_evidence()
                self.result['rac3Evidence'][field] = value
                self.save(self.folder / 'result.json', self.result)
                self.assertEqual(self.children(), [])

    def test_scalar_address_writes_shadowing_arrays_or_volatile_stay_manual(self):
        cases = [self.source.replace('return ' + self.symbol, 'return *(&' + self.symbol + ')'),
                 self.source.replace('return ' + self.symbol, self.symbol + ' = 1; return ' + self.symbol),
                 self.source.replace('extern f32', 'extern volatile f32'),
                 self.source.replace(self.symbol + ';', self.symbol + '[1];'),
                 self.source.replace('(void) {', '(void) { f32 ' + self.symbol + ';')]
        for source in cases:
            with self.subTest(source=source):
                self.source = source
                self.install_evidence()
                self.assertEqual(self.children(), [])

    def test_native_gp_modification_and_duplicate_placements_fail_closed(self):
        self.raw['level_01'] = struct.pack('<IIII', 0x279C0004, 0xC7800040, 0x03E00008, 0)
        self.places[0] = ('level_01', '.text', 0x100000, 16)
        self.install_evidence()
        self.assertEqual(self.children(), [])
        self.places.append(self.places[0])
        self.install_evidence()
        self.assertEqual(self.children(), [])

    def test_r5900_mult_optional_destination_cannot_preserve_gp_provenance(self):
        for function in (24, 25):
            with self.subTest(function=function):
                mult = (4 << 21) | (5 << 16) | (28 << 11) | function
                self.raw['level_01'] = struct.pack('<IIII', mult, 0xC7800040, 0x03E00008, 0)
                self.places = [('level_01', '.text', 0x100000, 16)]
                self.install_evidence()
                self.assertEqual(self.children(), [])
if __name__ == '__main__':
    unittest.main()
