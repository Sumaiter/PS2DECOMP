"""Synthetic access/ownership counterexamples; no retail bytes or toolchains."""
import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
from RAC3_PAL.symbolic_memory import children
from search_rules import Variants

NAME = 'func_L02_00001000'


def words(*items):
    return struct.pack('<' + 'I' * len(items), *items)


def load(address, op=35, register=8, value=2):
    low = address & 0xffff
    high = (address + (0x10000 if low & 0x8000 else 0)) >> 16
    return words(15 << 26 | register << 16 | high,
                 op << 26 | register << 21 | value << 16 | low,
                 0x03e00008, 0)


class Section(dict):
    pass


class Elf:
    elfclass = 32
    little_endian = True

    def __init__(self, base, write=True, code=False, nobits=False):
        self.section = Section(sh_flags=2 | int(write) | (4 if code else 0),
                               sh_addr=base, sh_size=0x1000,
                               sh_type='SHT_NOBITS' if nobits else 'SHT_PROGBITS')

    def __getitem__(self, key):
        return 'EM_MIPS' if key == 'e_machine' else None

    def iter_sections(self):
        return iter([self.section])


class Engine:
    def __init__(self, first=0x200000, second=0x210000, op=35):
        self.root = APP / 'tests/fixtures/project'
        self.raw = {'level_02': load(first, op), 'level_03': load(second, op)}
        self.elfs = {'level_02': Elf(0x200000), 'level_03': Elf(0x210000)}
        self.gps = {'level_02': 0x200000, 'level_03': 0x210000, 'frontbin': 0x180000}
        self.references = []

    def place(self, name):
        return self.all_places(name)[0]

    def all_places(self, name):
        return [(program, '.text', 0x1000, len(raw)) for program, raw in self.raw.items()]

    def assert_reference(self, program):
        self.references.append(program)

    def validated_gp(self, program):
        return self.gps[program]

    def disc_bytes(self, place):
        return self.raw[place[0]]

    def elf(self, program):
        return self.elfs[program]


class SymbolicMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = Variants(APP / 'tests/fixtures/project')

    def source(self, body='return *(s32 *)0x200000;', prefix='', result='s32', args='void'):
        return prefix + result + ' ' + NAME + '(' + args + '){' + body + '}'

    def run_rule(self, source=None, engine=None, rules=None):
        return children(engine or Engine(), NAME, source or self.source(), rules or self.rules)

    def test_level_names_preserve_type_access_and_unknown_extent(self):
        engine = Engine()
        output = self.run_rule(engine=engine)
        self.assertEqual(len(output), 1)
        self.assertIn('extern s32 D_L02_00200000[];', output[0][1])
        self.assertIn('return D_L02_00200000[0];', output[0][1])
        self.assertEqual(engine.references, ['level_02', 'level_03'])
        self.assertNotIn('= 0', output[0][1])

    def test_bss_is_named_but_initial_value_is_never_substituted(self):
        engine = Engine()
        engine.elfs['level_03'].section['sh_type'] = 'SHT_NOBITS'
        self.assertEqual(len(self.run_rule(engine=engine)), 1)

    def test_typed_float_store_order_is_unchanged(self):
        engine = Engine(op=57)
        source = self.source('*(f32 *)0x200000 = x; callback();', result='void', args='f32 x',
                             prefix='void callback(void);')
        output = self.run_rule(source, engine)[0][1]
        self.assertIn('extern f32 D_L02_00200000[];', output)
        self.assertLess(output.index('D_L02_00200000[0] = x;'), output.index('callback();'))

    def test_gp_slot_requires_correspondence_at_every_placement(self):
        engine = Engine()
        engine.raw = {program: words(35 << 26 | 28 << 21 | 2 << 16 | 0x10,
                                     0x03e00008, 0) for program in engine.raw}
        output = self.run_rule(self.source('return *(s32 *)0x200010;'), engine)[0][1]
        self.assertIn('extern s32 D_00180010_sda;', output)
        self.assertIn('return D_00180010_sda;', output)
        engine.raw['level_03'] = words(35 << 26 | 28 << 21 | 2 << 16 | 0x14, 0x03e00008, 0)
        # Although both accesses are valid data, their GP slots differ.
        self.assertEqual(self.run_rule(self.source('return *(s32 *)0x200010;'), engine), [])

    def test_negative_evidence_matrix(self):
        cases = []
        engine = Engine(); engine.elfs['level_03'].section['sh_addr'] += 0x1000
        cases.append(('unallocated_secondary', self.source(), engine))
        engine = Engine(); engine.elfs['level_02'].section['sh_flags'] |= 4
        cases.append(('function_text', self.source(), engine))
        engine = Engine(); engine.raw['level_03'] = load(0x210000, register=9)
        cases.append(('different_native_register', self.source(), engine))
        engine = Engine(); engine.raw['level_03'] = load(0x210000, op=49)
        cases.append(('different_access_type', self.source(), engine))
        engine = Engine(); engine.raw['level_03'] = words(15 << 26 | 8 << 16 | 0x21,
            12 << 26 | 8 << 21 | 8 << 16, 35 << 26 | 8 << 21 | 2 << 16, 0x03e00008, 0)
        cases.append(('reassigned_base', self.source(), engine))
        engine = Engine(); engine.raw['level_03'] = words(15 << 26 | 8 << 16 | 0x21,
            3 << 26 | 0x1000, 0, 35 << 26 | 8 << 21 | 2 << 16, 0x03e00008, 0)
        cases.append(('clobbered_call_base', self.source(), engine))
        engine = Engine(); engine.raw['level_03'] = words(15 << 26 | 8 << 16 | 0x21,
            4 << 26 | 4 << 21 | 1, 0, 35 << 26 | 8 << 21 | 2 << 16, 0x03e00008, 0)
        cases.append(('branch_merge', self.source(), engine))
        engine = Engine(); engine.raw['level_03'] = engine.raw['level_03'][:-4]
        cases.append(('missing_return_delay', self.source(), engine))
        engine = Engine(); engine.elfs['level_02'].elfclass = 64
        cases.append(('non_ee_elf', self.source(), engine))
        engine = Engine(); engine.elfs['level_02'].little_endian = False
        cases.append(('wrong_endianness', self.source(), engine))
        engine = Engine(op=43); engine.elfs['level_02'].section['sh_flags'] = 2
        cases.append(('store_read_only_data', self.source('*(s32 *)0x200000=1;',result='void'), engine))
        cases.extend((label, source, Engine()) for label, source in [
            ('volatile', self.source('return *(volatile s32 *)0x200000;')),
            ('qualified_alias', self.source('return *(Alias *)0x200000;', prefix='typedef volatile int Alias;')),
            ('wide_alias', self.source('return *(s64 *)0x200000;')),
            ('abi_override', self.source(prefix='typedef unsigned short s32;')),
            ('misaligned', self.source('return *(s32 *)0x200001;')),
            ('no_native_address', self.source('return *(s32 *)0x300000;')),
            ('computed_pointer', self.source('return *(s32 *)(0x200000+4);')),
            ('local_pointer', self.source('s32 *p=(s32 *)0x200000;return *p;')),
            ('compound_assignment', self.source('*(s32 *)0x200000+=1;return 0;')),
            ('address_escape', self.source('return (s32)&*(s32 *)0x200000;')),
            ('macro', self.source(prefix='#define s32 short\n')),
            ('builtin', self.source('return *(s32 *)0x200000 + __LINE__;')),
            ('symbol_conflict', self.source(prefix='extern s32 D_L02_00200000;')),
            ('global_definition', self.source(prefix='s32 unrelated=1;')),
            ('mixed_access_types', self.source('return *(s32 *)0x200000 + (s32)*(f32 *)0x200000;')),
        ])
        for label, source, engine in cases:
            with self.subTest(label=label):
                self.assertEqual(self.run_rule(source, engine), [])

    def test_ambiguous_secondary_targets_reject(self):
        engine=Engine()
        engine.raw['level_02']=words(15<<26|8<<16|0x20,35<<26|8<<21|2<<16,
                                     35<<26|8<<21|3<<16,0x03e00008,0)
        engine.raw['level_03']=words(15<<26|8<<16|0x21,35<<26|8<<21|2<<16,
                                     35<<26|8<<21|3<<16|4,0x03e00008,0)
        self.assertEqual(self.run_rule(engine=engine),[])

    def test_cached_ast_is_not_mutated(self):
        source=self.source()
        tree=self.rules.parse(source)
        before=self.rules.generator.visit(tree[0])
        rules=copy.copy(self.rules)
        rules.parse=lambda text: tree
        self.assertEqual(len(self.run_rule(source,rules=rules)),1)
        self.assertEqual(self.rules.generator.visit(tree[0]),before)

    def test_primary_read_write_alias_must_hold_in_every_level(self):
        engine = Engine()
        engine.raw['level_02'] = words(15 << 26 | 8 << 16 | 0x20,
            35 << 26 | 8 << 21 | 2 << 16,
            43 << 26 | 8 << 21 | 2 << 16, 0x03e00008, 0)
        engine.raw['level_03'] = words(15 << 26 | 8 << 16 | 0x21,
            35 << 26 | 8 << 21 | 2 << 16,
            43 << 26 | 8 << 21 | 2 << 16 | 4, 0x03e00008, 0)
        source = self.source('s32 value;value=*(s32 *)0x200000;*(s32 *)0x200000=value;return value;')
        self.assertEqual(self.run_rule(source, engine), [])

    def test_same_address_float_read_does_not_validate_integer_cast(self):
        engine = Engine(op=49)
        self.assertEqual(self.run_rule(engine=engine), [])

    def test_jal_kills_stale_ra_before_its_delay_slot(self):
        engine = Engine()
        for program, high in [('level_02', 0x20), ('level_03', 0x21)]:
            engine.raw[program] = words(15 << 26 | 31 << 16 | high,
                3 << 26 | 0x1000, 35 << 26 | 31 << 21 | 2 << 16,
                0x03e00008, 0)
        self.assertEqual(self.run_rule(engine=engine), [])

    def test_r5900_three_operand_mult_kills_the_old_rd(self):
        for fn in (24, 25):
            with self.subTest(instruction=fn):
                engine = Engine()
                for program, high in [('level_02', 0x20), ('level_03', 0x21)]:
                    engine.raw[program] = words(15 << 26 | 8 << 16 | high,
                        4 << 21 | 5 << 16 | 8 << 11 | fn,
                        35 << 26 | 8 << 21 | 2 << 16, 0x03e00008, 0)
                self.assertEqual(self.run_rule(engine=engine), [])

    def test_local_typedef_cannot_change_the_new_global_declaration(self):
        source = self.source('typedef unsigned short s32;return *(s32 *)0x200000;')
        self.assertEqual(self.run_rule(source), [])

    def test_overlapping_globals_never_become_independent_extern_objects(self):
        engine = Engine()
        for program, high in [('level_02', 0x20), ('level_03', 0x21)]:
            engine.raw[program] = words(15 << 26 | 8 << 16 | high,
                43 << 26 | 8 << 21 | 2 << 16,
                40 << 26 | 8 << 21 | 2 << 16 | 1,
                35 << 26 | 8 << 21 | 2 << 16, 0x03e00008, 0)
        source = self.source('*(u32 *)0x200000=0x11223344;*(u8 *)0x200001=0xff;return *(u32 *)0x200000;')
        self.assertEqual(self.run_rule(source, engine), [])

    def test_header_macros_and_conditional_types_are_not_a_prelude(self):
        for addition in ('#define s32 short\n', '#if 1\n', '#define f32 volatile float\n'):
            with self.subTest(directive=addition):
                rules = copy.copy(self.rules)
                rules.header_text = addition + rules.header_text
                self.assertEqual(self.run_rule(rules=rules), [])

    def test_rac3_metadata_header_is_allowed_but_cannot_hide_a_type_macro(self):
        rules = copy.copy(self.rules)
        rules.header_text = rules.header_text.replace('#define NULL ((void *)0)', '#include "include_asm.h"')
        with tempfile.TemporaryDirectory() as temp:
            engine = Engine()
            engine.root = Path(temp)
            (engine.root / 'include').mkdir()
            (engine.root / 'include/common.h').write_text(rules.header_text, encoding='utf-8', newline='\n')
            header = engine.root / 'include/include_asm.h'
            valid = r'''#define UNUSED_STUB(X) X
__asm__(".include \"include/labels.inc\"\n");
'''
            header.write_text(valid, encoding='utf-8', newline='\n')
            self.assertEqual(len(self.run_rule(engine=engine, rules=rules)), 1)
            for poison in ('#define s32 short\n', 'typedef volatile int hidden;\n', '#include "foreign.h"\n'):
                with self.subTest(poison=poison):
                    header.write_text(valid + poison, encoding='utf-8', newline='\n')
                    self.assertEqual(self.run_rule(engine=engine, rules=rules), [])

    def test_distinct_primary_ranges_must_remain_disjoint_elsewhere(self):
        engine = Engine()
        for program, high, byte_offset in [('level_02', 0x20, 8), ('level_03', 0x21, 1)]:
            engine.raw[program] = words(15 << 26 | 8 << 16 | high,
                43 << 26 | 8 << 21 | 2 << 16,
                40 << 26 | 8 << 21 | 2 << 16 | byte_offset,
                35 << 26 | 8 << 21 | 2 << 16, 0x03e00008, 0)
        source = self.source('*(u32 *)0x200000=1;*(u8 *)0x200008=2;return *(u32 *)0x200000;')
        self.assertEqual(self.run_rule(source, engine), [])

    def test_existing_data_declaration_or_escaped_literal_pointer_stays_manual(self):
        cases = [
            self.source(prefix='extern s32 another_name;'),
            self.source('s32 *p=(s32 *)0x200000;return *(s32 *)0x200000 + *p;'),
            self.source('callback((s32 *)0x200000);return *(s32 *)0x200000;',
                        prefix='void callback(s32 *);'),
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(self.run_rule(source), [])

    def test_reference_failure_is_fail_closed(self):
        engine=Engine()
        def fail(program):
            raise RuntimeError('changed reference')
        engine.assert_reference=fail
        self.assertEqual(self.run_rule(engine=engine),[])

    def test_provider_cannot_substitute_a_consistent_but_foreign_typedef_header(self):
        rules = copy.copy(self.rules)
        rules.header_text = rules.header_text.replace('typedef int s32;', 'typedef short s32;')
        rules.prelude = rules.prelude.replace('typedef int s32;', 'typedef short s32;')
        self.assertNotEqual(rules.header_text, self.rules.header_text)
        self.assertEqual(self.run_rule(rules=rules), [])


if __name__=='__main__':
    unittest.main()
