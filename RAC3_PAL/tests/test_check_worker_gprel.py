"""Named GP-relative target proofs and counterexamples; synthetic bytes only."""
import copy
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from RAC3_PAL.check_worker import (_level_references, _named_gp_level_targets,
                                  _named_gp_object_scope)


NAME = 'D_L02_00200400'


def words(*items):
    return struct.pack('<' + 'I' * len(items), *items)


def memory(displacement, op=49, value=12, base=28):
    return op << 26 | base << 21 | value << 16 | (displacement & 0xffff)


def body(displacement=0, **kwargs):
    return words(memory(displacement, **kwargs), 0x03e00008, 0)


def reference(displacement, gp=0x200000, **kwargs):
    return dict(address=0x1000, raw=body(displacement, **kwargs), gp=gp,
                data=[(gp, gp + 0x1000, True)])


def synthetic_derive(data, native, relocations, address, gp):
    """The injected project's REL GPREL16 protocol, without game fixtures."""
    if len(data) != len(native):
        raise ValueError('length')
    values = {}
    for offset, kind, symbol in relocations:
        if kind != 7:
            raise ValueError('unsupported relocation')
        ours, theirs = struct.unpack_from('<I', data, offset)[0], struct.unpack_from('<I', native, offset)[0]
        if (ours ^ theirs) & 0xffff0000:
            raise ValueError('instruction differs')
        signed = lambda value: (value & 0x7fff) - (value & 0x8000)
        target = (gp + signed(theirs) - signed(ours)) & 0xffffffff
        if values.setdefault(symbol, target) != target:
            raise ValueError('inconsistent target')
    return values


class NamedGpTargetTests(unittest.TestCase):
    def setUp(self):
        self.body = body()
        self.relocations = [(0, 7, NAME)]
        self.references = {2: reference(0x400), 3: reference(0x600),
                           4: reference(0x440, gp=0x210000)}

    def run_rule(self, **kwargs):
        values = dict(body=self.body, relocations=self.relocations, references=self.references,
                      primary=2, derive=synthetic_derive, blocked_symbols=())
        values.update(kwargs)
        return _named_gp_level_targets(**values)

    def assert_rejected(self, **kwargs):
        self.assertEqual(self.run_rule(**kwargs), ({}, []))

    def test_moving_named_object_keeps_each_actual_gp_target(self):
        mapped, evidence = self.run_rule()
        self.assertEqual(mapped, {NAME: {2: 0x200400, 3: 0x200600, 4: 0x210440}})
        self.assertEqual(evidence[0]['relocationType'], 7)
        self.assertEqual(evidence[0]['policy'], 'tools.level_symbols.derive')
        self.assertEqual([p['uses'][0]['gpOffset'] for p in evidence[0]['placements']],
                         [0x400, 0x600, 0x440])
        self.assertEqual([p['uses'][0]['width'] for p in evidence[0]['placements']], [4, 4, 4])

    def test_fixed_sda_slots_are_never_retargeted_by_this_rule(self):
        for name in ('D_00200400_sda', NAME + '_sda', NAME + '_sda_alias'):
            with self.subTest(name=name):
                self.assert_rejected(relocations=[(0, 7, name)])
        # A moving object's name proves the observed different slots; changing
        # it to a fixed-slot alias would not have the same meaning.
        mapped, _ = self.run_rule()
        self.assertNotEqual(mapped[NAME][2] - self.references[2]['gp'],
                            mapped[NAME][3] - self.references[3]['gp'])

    def test_positive_negative_and_sign_boundary_rel_addends_are_retained(self):
        for addend in (4, -4, 32767, -32768):
            with self.subTest(addend=addend):
                refs = copy.deepcopy(self.references)
                for number, ref in refs.items():
                    target = {2: 0x200400, 3: 0x200600, 4: 0x210440}[number]
                    effective = target + addend
                    ref.update(raw=body(effective - ref['gp'], op=36),
                               data=[(target - 0x9000, target + 0x9000, True)])
                # Extreme addends need a GP near the effective address so the
                # native displacement, too, remains a signed 16-bit quantity.
                if abs(addend) > 4:
                    for number, ref in refs.items():
                        target = {2: 0x200400, 3: 0x200600, 4: 0x210440}[number]
                        ref['gp'] = target
                        ref['raw'] = body(addend, op=36)
                mapped, evidence = self.run_rule(body=body(addend, op=36), references=refs)
                self.assertEqual(mapped[NAME], {2: 0x200400, 3: 0x200600, 4: 0x210440})
                self.assertTrue(all(p['uses'][0]['addend'] == addend for p in evidence[0]['placements']))

    def test_negative_native_gp_displacements_are_supported(self):
        name = 'D_L02_001FFC00'
        refs = {2: reference(-0x400), 3: reference(-0x600)}
        for ref in refs.values():
            ref['data'] = [(0x1ff000, 0x200000, True)]
        mapped, _ = self.run_rule(relocations=[(0, 7, name)], references=refs)
        self.assertEqual(mapped, {name: {2: 0x1ffc00, 3: 0x1ffa00}})

    def test_every_canonical_reference_is_required_and_failure_leaves_no_partial_map(self):
        for change in ('short', 'missing_gp', 'missing_data', 'wrong_instruction'):
            with self.subTest(change=change):
                refs = copy.deepcopy(self.references)
                if change == 'short':
                    refs[4]['raw'] = b''
                elif change == 'missing_gp':
                    del refs[4]['gp']
                elif change == 'missing_data':
                    del refs[4]['data']
                else:
                    refs[4]['raw'] = body(0x440, value=13)
                self.assert_rejected(references=refs)

    def test_primary_name_anchor_and_its_level_must_agree(self):
        for name in ('D_L03_00200400', 'D_L02_00200404', 'arbitrary_global', 'D_00200400'):
            with self.subTest(name=name):
                self.assert_rejected(relocations=[(0, 7, name)])
        self.assert_rejected(references={3: self.references[3]})
        self.assert_rejected(primary=3)
        self.assert_rejected(primary=True)

    def test_unknown_or_invalid_gp_fails_closed(self):
        for gp in (None, True, False, 0, -1, 1 << 32):
            with self.subTest(gp=gp):
                refs = copy.deepcopy(self.references)
                refs[3]['gp'] = gp
                self.assert_rejected(references=refs)

    def test_changed_op_base_or_destination_cannot_be_resolved(self):
        for changed in (body(0x600, base=27), body(0x600, op=35), body(0x600, value=13)):
            refs = copy.deepcopy(self.references)
            refs[3]['raw'] = changed
            self.assert_rejected(references=refs)
        for candidate in (body(base=27), body(op=9), body(op=35, value=28),
                          body(op=30), body(op=31), body(op=55), body(op=63)):
            self.assert_rejected(body=candidate)

    def test_gp_clobbers_and_control_edges_before_access_are_rejected(self):
        multiply_to_gp = 4 << 21 | 5 << 16 | 28 << 11 | 24
        for prefix in (0x3c1c0020, 0x8c9c0000, 0x789c0000, 0x441c0000,
                       multiply_to_gp, multiply_to_gp | 1, 0x0080e009,
                       0x0c001000, 0x10000001):
            with self.subTest(prefix=hex(prefix)):
                raw = words(prefix, 0) + self.body
                refs = {n: dict(ref, raw=words(prefix, 0) + ref['raw'])
                        for n, ref in self.references.items()}
                self.assert_rejected(body=raw, relocations=[(8, 7, NAME)], references=refs)

    def test_native_gp_clobber_is_rejected_even_if_candidate_gp_is_unchanged(self):
        raw = words(0, 0) + self.body
        refs = {n: dict(ref, raw=words(0, 0) + ref['raw']) for n, ref in self.references.items()}
        refs[3]['raw'] = words(0x3c1c0020, 0) + self.references[3]['raw']
        self.assert_rejected(body=raw, relocations=[(8, 7, NAME)], references=refs)

    def test_unrelated_register_writes_do_not_falsely_invalidate_gp(self):
        prefix = words(0x24080001)
        refs = {n: dict(ref, raw=prefix + ref['raw']) for n, ref in self.references.items()}
        self.assertTrue(self.run_rule(body=prefix + self.body,
                                    relocations=[(4, 7, NAME)], references=refs)[0])

    def test_memory_width_alignment_bounds_and_write_rights_are_checked(self):
        for op, width, write in ((32, 1, False), (36, 1, False), (40, 1, True),
                                 (33, 2, False), (37, 2, False), (41, 2, True),
                                 (35, 4, False), (43, 4, True), (49, 4, False), (57, 4, True)):
            with self.subTest(op=op):
                refs = {n: dict(ref, raw=body(struct.unpack_from('<I', ref['raw'])[0] & 0xffff, op=op))
                        for n, ref in self.references.items()}
                self.assertTrue(self.run_rule(body=body(op=op), references=refs)[0])
                refs[3]['data'] = [(0x200600, 0x200600 + width - 1, True)]
                self.assert_rejected(body=body(op=op), references=refs)
                refs[3]['data'] = [(0x200600, 0x200600 + width, not write)]
                result = self.run_rule(body=body(op=op), references=refs)[0]
                self.assertEqual(bool(result), not write)
        refs = {n: dict(ref, raw=body((struct.unpack_from('<I', ref['raw'])[0] & 0xffff) + 1))
                for n, ref in self.references.items()}
        self.assert_rejected(body=body(1), references=refs)

    def test_symbol_base_and_effective_access_must_both_be_in_data(self):
        refs = copy.deepcopy(self.references)
        refs[3]['data'] = [(0x200604, 0x200608, True)]
        refs[3]['raw'] = body(0x604)
        # The actual four-byte access is in data, but its proposed base is not.
        self.assert_rejected(body=body(4), references=refs)

    def test_address_wrap_and_overflow_are_rejected(self):
        refs = copy.deepcopy(self.references)
        refs[3].update(gp=0xfffffff0, raw=body(0x20), data=[(0, 1 << 32, True)])
        self.assert_rejected(references=refs)

    def test_mixed_hidden_duplicate_or_malformed_relocation_groups_are_rejected(self):
        for relocs in ([(0, 7, NAME), (0, 7, NAME)], [(0, 5, NAME)],
                       [(0, 7, NAME), (4, 6, NAME)], [(0, 8, NAME)],
                       [(0, 2, NAME)], [(-4, 7, NAME)], [(2, 7, NAME)],
                       [(100, 7, NAME)]):
            with self.subTest(relocs=relocs):
                self.assert_rejected(relocations=relocs)
        self.assert_rejected(blocked_symbols={NAME})
        self.assert_rejected(body=self.body[:-1])

    def test_repeated_uses_require_one_consistent_target_and_preserve_addends(self):
        raw = words(memory(0), memory(4), 0x03e00008, 0)
        refs = {n: dict(ref, raw=words(memory(d), memory(d + 4), 0x03e00008, 0))
                for n, ref in self.references.items()
                for d in [(struct.unpack_from('<I', ref['raw'])[0] & 0xffff)]}
        relocs = [(0, 7, NAME), (4, 7, NAME)]
        self.assertTrue(self.run_rule(body=raw, references=refs, relocations=relocs)[0])
        refs[3]['raw'] = words(memory(0x600), memory(0x608), 0x03e00008, 0)
        self.assert_rejected(body=raw, references=refs, relocations=relocs)

    def test_other_wrong_words_are_never_substituted_or_approved(self):
        candidate = self.body + words(0x24020001)
        refs = {n: dict(ref, raw=ref['raw'] + words(0x24020002)) for n, ref in self.references.items()}
        self.assertTrue(self.run_rule(body=candidate, references=refs)[0])
        self.assertNotEqual(candidate[-4:], refs[2]['raw'][-4:])
        self.assertEqual(candidate[-4:], words(0x24020001))

    def test_project_policy_exception_or_extra_value_is_not_a_target_proof(self):
        def fail(*args):
            raise ValueError('project derive refused')
        self.assert_rejected(derive=fail)
        self.assert_rejected(derive=lambda *args: {NAME: 0x200400, 'other': 0})


class Section(dict):
    def __init__(self, name, data=b'', flags=0, kind='SHT_PROGBITS'):
        super().__init__(sh_flags=flags, sh_size=len(data), sh_type=kind, sh_addr=0x1000)
        self.name, self.payload = name, data

    def data(self):
        return self.payload


class Symbol(dict):
    def __init__(self, name, section='SHN_UNDEF', kind='STT_NOTYPE', bind='STB_GLOBAL', start=0, size=0):
        super().__init__(st_shndx=section, st_info=dict(type=kind, bind=bind), st_value=start, st_size=size)
        self.name = name


class Symbols(Section):
    def __init__(self, symbols):
        super().__init__('.symtab')
        self.symbols = symbols

    def iter_symbols(self):
        return iter(self.symbols)

    def get_symbol(self, index):
        return self.symbols[index]


class Relocations(Section):
    def __init__(self, offset=0, info=0, rela=False):
        super().__init__('.rela.text' if rela else '.rel.text')
        self.update(sh_link=1, sh_info=info)
        self.rela = rela
        self.records = [dict(r_offset=offset, r_info_sym=0, r_info_type=7)]

    def is_RELA(self):
        return self.rela

    def iter_relocations(self):
        return iter(self.records)


class Elf(dict):
    elfclass, little_endian = 32, True

    def __init__(self, sections):
        super().__init__(e_machine='EM_MIPS', e_type='ET_REL')
        self.sections = sections

    def iter_sections(self):
        return iter(self.sections)

    def get_section(self, index):
        return self.sections[index]

    def get_section_by_name(self, name):
        return next((section for section in self.sections if section.name == name), None)

    def get_section_index(self, name):
        return next((i for i, section in enumerate(self.sections) if section.name == name), None)


class NamedGpObjectScopeTests(unittest.TestCase):
    def setUp(self):
        self.function = Symbol('func_L02_00001000', section=0, kind='STT_FUNC', size=12)
        self.elf = Elf([Section('.text', body(), flags=6), Symbols([Symbol(NAME)]), Relocations()])

    def scope(self):
        with patch('elftools.elf.relocation.RelocationSection', Relocations):
            return _named_gp_object_scope(self.elf, self.function)

    def test_exact_undefined_data_symbol_is_allowed(self):
        self.assertEqual(self.scope(), set())

    def test_invalid_elf_class_endianness_machine_or_object_kind_fails_closed(self):
        for field, value in (('elfclass', 64), ('little_endian', False),
                             ('e_machine', 'EM_386'), ('e_type', 'ET_EXEC')):
            with self.subTest(field=field):
                if field in ('elfclass', 'little_endian'):
                    setattr(self.elf, field, value)
                else:
                    self.elf[field] = value
                self.assertIsNone(self.scope())
                self.setUp()

    def test_function_symbol_type_alignment_and_complete_text_extent_are_required(self):
        for key, value in (('st_value', -4), ('st_value', 2), ('st_value', 4),
                           ('st_size', 0), ('st_size', 10), ('st_size', 16),
                           ('st_size', True), ('st_shndx', 'SHN_UNDEF')):
            with self.subTest(key=key, value=value):
                self.function[key] = value
                self.assertIsNone(self.scope())
                self.setUp()
        self.function['st_info']['type'] = 'STT_OBJECT'
        self.assertIsNone(self.scope())
        self.setUp()
        self.elf.sections[0]['sh_flags'] = 2
        self.assertIsNone(self.scope())
        self.elf.sections[0]['sh_flags'] = 4
        self.assertIsNone(self.scope())

    def test_defined_duplicate_section_and_nonglobal_symbols_are_blocked(self):
        for symbol in (Symbol(NAME, section=0), Symbol(NAME, section='SHN_ABS'),
                       Symbol(NAME, kind='STT_SECTION'), Symbol(NAME, kind='STT_FUNC'),
                       Symbol(NAME, bind='STB_LOCAL'), Symbol(NAME, bind='STB_WEAK')):
            with self.subTest(symbol=symbol):
                self.elf.sections[1].symbols.append(symbol)
                self.assertIn(NAME, self.scope())
                self.setUp()

    def test_rela_and_allocated_literal_or_data_sections_are_not_supported(self):
        self.elf.sections[2].rela = True
        self.assertIsNone(self.scope())
        for name in ('.lit4', '.rodata', '.data', '.bss'):
            self.setUp()
            self.elf.sections.append(Section(name, words(0), flags=2))
            self.assertIsNone(self.scope())

    def test_other_sections_and_outside_body_uses_cannot_hide_groups(self):
        for offset, info in ((12, 0), (-4, 0), (0, 1)):
            with self.subTest(offset=offset, info=info):
                self.elf.sections.append(Relocations(offset=offset, info=info))
                self.assertIn(NAME, self.scope())
                self.setUp()

    def test_relocation_and_resolver_must_use_the_same_symbol_table(self):
        self.elf.sections.append(Symbols([Symbol(NAME)]))
        self.elf.sections[2]['sh_link'] = 3
        self.assertIsNone(self.scope())


class CanonicalReferenceCollectorTests(unittest.TestCase):
    def collect(self, *, missing=None, wrong_hash=None, wrong_gp=None, wrong_format=None):
        name = 'func_L02_00001000'
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            expected, paths = {}, {}
            for level in (2, 3, 4):
                blob = ('synthetic-reference-' + str(level)).encode()
                paths[level] = base / str(level)
                paths[level].write_bytes(blob)
                expected[level] = hashlib.sha1(blob).hexdigest()
            row = (name, 'shared', 12, '', False, [(2, 0x1000), (3, 0x1000), (4, 0x1000)])
            def target(name):
                level = int(name[-2:])
                if level == missing:
                    raise FileNotFoundError('missing canonical reference')
                return SimpleNamespace(path=lambda _: paths[level], gp=0x200000,
                    sha1='wrong' if level == wrong_hash else expected[level])
            def fake_elf(stream):
                level = int(stream.getvalue().decode()[-1])
                elf = Elf([Section('.text', body(0x400), flags=6),
                           Section('.bss', b'\0' * 0x1000, flags=3, kind='SHT_NOBITS')])
                elf.sections[1]['sh_addr'] = 0x200000
                if level == wrong_format:
                    elf.little_endian = False
                return elf
            def gp_value(path):
                return (None if int(Path(path).name) == wrong_gp else 0x200000), None
            with patch.dict(sys.modules, {
                    'gp': SimpleNamespace(from_elf=gp_value),
                    'level_catalogue': SimpleNamespace(read_catalogue=lambda: [row]),
                    'targets': SimpleNamespace(get=target)}), patch('elftools.elf.elffile.ELFFile', fake_elf):
                return _level_references(name)

    def test_collector_supplies_all_canonical_placements(self):
        primary, references = self.collect()
        self.assertEqual(primary, 2)
        self.assertEqual(set(references), {2, 3, 4})

    def test_missing_or_invalid_secondary_never_returns_a_partial_reference_map(self):
        with self.assertRaises(FileNotFoundError):
            self.collect(missing=4)
        for change in ('wrong_hash', 'wrong_gp', 'wrong_format'):
            with self.subTest(change=change):
                self.assertIsNone(self.collect(**{change: 4}))


if __name__ == '__main__':
    unittest.main()
