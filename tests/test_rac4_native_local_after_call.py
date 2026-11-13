"""Pure C1 integration tests: synthetic native bytes and parser fixtures only."""
from pathlib import Path
import copy
import struct
import sys
import types
import unittest

FOLDER = Path(__file__).resolve().parent
ADDON = Path(__file__).resolve().parents[1]
RUNTIME = (Path(__file__).parent / 'fixtures/project')
from search_rules import Variants
from RAC4_PAL.native_local_after_call import analyze, generate
from RAC4_PAL.native_local_after_call_wrapper import generate_for_engine

NAME = 'func_00001000'
CALLEE = 'func_00002000'
SOURCE = '''#include "common.h"
s32 func_00002000(void);
s32 func_00001000(void) {
    s32 value;
    s32 result;
    value = -1;
    result = func_00002000();
    return value + result;
}
'''


def native(target=0x2000, value=-1):
    # Balanced synthetic MIPS call/return; no original function is loaded.
    words = [0x27bdfff0, 0xffbf0000, 0x0c000000 | (target >> 2), 0,
             0x24060000 | (value & 0xffff), 0xdfbf0000, 0x03e00008, 0x27bd0010]
    return struct.pack('<8I', *words)


def patch(raw, index, word):
    out = bytearray(raw)
    struct.pack_into('<I', out, index * 4, word)
    return bytes(out)


class FakeEngine:
    def __init__(self):
        self.root = RUNTIME
        self.events = []
        self.primary = ('level_01', '.text', 0x1000, 32)
        self.places = [self.primary, ('level_02', '.text', 0x3000, 32)]
        self.symbols = {'level_01': 0x2000, 'level_02': 0x4000}
        self.raw = {self.primary: native(), self.places[1]: native(0x4000)}
        self.reference_error = None
        self.reference_return = None
        self.check = types.SimpleNamespace(disc_bytes=self.disc_bytes)

    def place(self, name):
        self.events.append(('place', name))
        return self.primary

    def all_places(self, name):
        self.events.append(('all_places', name))
        return self.places

    def assert_reference(self, program):
        self.events.append(('reference', program))
        if self.reference_error:
            raise self.reference_error
        return self.reference_return

    def symbol_address(self, name, program):
        self.events.append(('symbol', program, name))
        return self.symbols[program]

    def disc_bytes(self, place):
        self.events.append(('raw', place))
        return self.raw[tuple(place)]


class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.rules = Variants(RUNTIME)
        self.engine = FakeEngine()

    def run_rule(self, source=SOURCE, name=NAME, rules=None):
        return generate_for_engine(self.engine, name, source, rules=rules or self.rules)

    def rejected_before_engine(self, source=SOURCE, name=NAME, rules=None):
        self.engine = FakeEngine()
        self.assertEqual(self.run_rule(source, name, rules), [])
        self.assertEqual(self.engine.events, [])

    def test_positive_emits_one_swapped_assignment_pair(self):
        out = self.run_rule()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], 'native-local-constant-after-call')
        a = self.rules.a
        before, _ = self.rules.parse(SOURCE)
        after, fn = self.rules.parse(out[0][1])
        assignments = [n for n in fn.body.block_items if isinstance(n, a.Assignment)]
        self.assertEqual([self.rules.generator.visit(n) for n in assignments],
                         ['result = func_00002000()', 'value = -1'])
        # Restore those two nodes: every expression, declaration and call agrees.
        items = fn.body.block_items
        items[2:4] = items[3:4] + items[2:3]
        self.assertEqual(self.rules.generator.visit(before), self.rules.generator.visit(after))

    def test_all_references_precede_any_symbol_and_native_read(self):
        self.assertTrue(self.run_rule())
        events = self.engine.events
        refs = [i for i, e in enumerate(events) if e[0] == 'reference']
        access = [i for i, e in enumerate(events) if e[0] in ('symbol', 'raw')]
        self.assertEqual([events[i][1] for i in refs], ['level_01', 'level_02'])
        self.assertLess(max(refs), min(access))
        symbols = [i for i, e in enumerate(events) if e[0] == 'symbol']
        raw = [i for i, e in enumerate(events) if e[0] == 'raw']
        self.assertLess(max(symbols), min(raw))

    def test_same_program_multiple_placements_reference_once(self):
        place = ('level_01', '.text', 0x5000, 32)
        self.engine.places.append(place)
        self.engine.raw[place] = native()
        self.assertTrue(self.run_rule())
        self.assertEqual(self.engine.events.count(('reference', 'level_01')), 1)
        self.assertEqual(len([e for e in self.engine.events if e[0] == 'raw']), 3)

    def test_lazy_default_rules_and_preserved_cflags(self):
        src = '/* cflags: @ee29 */\n' + SOURCE
        out = generate_for_engine(self.engine, NAME, src)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0][1].startswith('/* cflags: @ee29 */\n'))

    def test_function_identity_and_name_format_before_engine(self):
        for name in ('func_00001004', 'sample', 'func_0000100a', '', None):
            with self.subTest(name=name):
                self.rejected_before_engine(name=name)

    def test_nonshape_and_invalid_syntax_before_engine(self):
        for source in ('s32 func_00001000(void) { return 0; }', None, '',
                       SOURCE.replace('value = -1;', 'value = ;')):
            with self.subTest(source=source):
                self.rejected_before_engine(source)

    def test_directives_splices_and_noncanonical_flags_before_engine(self):
        cases = ['#define value other\n' + SOURCE, '#pragma GCC optimize(2)\n' + SOURCE,
                 '#include <common.h>\n' + SOURCE, '/* \\\n */\n' + SOURCE,
                 '/* cflags: @ee29 */\n/* cflags: @ee29 */\n' + SOURCE,
                 '/* leading */\n/* cflags: @ee29 */\n' + SOURCE,
                 '/* cflags : @ee29 */\n' + SOURCE]
        for source in cases:
            with self.subTest(source=source):
                self.rejected_before_engine(source)

    def test_private_local_read_and_address_escape_before_engine(self):
        cases = [SOURCE.replace('func_00002000();', 'func_00002000(value);'),
                 SOURCE.replace('return value + result;', 'return *(&value) + result;'),
                 SOURCE.replace('s32 value;', 'static s32 value;'),
                 SOURCE.replace('s32 value;', 'volatile s32 value;'),
                 SOURCE.replace('s32 value;', 's32 value = 0;'),
                 SOURCE.replace('return value + result;', '{s32 value; value=0;} return result;')]
        for source in cases:
            with self.subTest(source=source):
                self.rejected_before_engine(source)

    def test_alignment_funcspec_asm_builtin_attributes_before_engine(self):
        cases = [SOURCE.replace('s32 value;', '_Alignas(16) s32 value;'),
                 SOURCE.replace('s32 value;', 's32 value __attribute__((aligned(16)));'),
                 SOURCE.replace('s32 value;', 'register s32 value;'),
                 SOURCE.replace('s32 value;', 's32 value __asm__("$s0");'),
                 SOURCE.replace('s32 func_00001000(', 'inline s32 func_00001000('),
                 SOURCE.replace('func_00002000', '__builtin_get'),
                 SOURCE.replace('return value + result;', '__asm__("nop"); return result;')]
        for source in cases:
            with self.subTest(source=source):
                self.rejected_before_engine(source)

    def test_prelude_recursive_qualifiers_attributes_cycles_duplicates_before_engine(self):
        for prelude in ('typedef volatile int I;typedef I J;typedef J s32;',
                        'typedef int I __attribute__((aligned(16)));typedef I s32;',
                        'typedef int s32 __attribute__((mode(DI)));',
                        'typedef int I;typedef I s32;typedef volatile int I;',
                        'typedef int I;typedef I J;typedef J I;typedef I s32;'):
            with self.subTest(prelude=prelude):
                rules = copy.copy(self.rules)
                rules.prelude = prelude
                rules.header_text = prelude
                self.rejected_before_engine(rules=rules)

    def test_unqualified_recursive_alias_accepted(self):
        rules = copy.copy(self.rules)
        rules.prelude = 'typedef signed int I;typedef I J;typedef J s32;'
        rules.header_text = rules.prelude
        self.assertTrue(self.run_rule(rules=rules))

    def test_callback_shadow_and_indirect_call_before_engine(self):
        source = SOURCE.replace('s32 result;', 's32 result; s32 (*func_00002000)(void);')
        self.rejected_before_engine(source)
        self.rejected_before_engine(SOURCE.replace('result = func_00002000();',
                                                   'result = (*func_00002000)();'))

    def test_unsigned_other_types_and_zero_before_engine(self):
        for replacement in ('u32 value;', 's16 value;', 's64 value;', 'f32 value;', 's32 *value;'):
            with self.subTest(replacement=replacement):
                self.rejected_before_engine(SOURCE.replace('s32 value;', replacement))
        for literal in ('0', '32768', '-32769', 'result'):
            with self.subTest(literal=literal):
                self.rejected_before_engine(SOURCE.replace('value = -1;', 'value = '+literal+';'))

    def test_unary_signed_limits_keep_identical_values(self):
        for value in (-32768, 32767, 1):
            with self.subTest(value=value):
                self.engine = FakeEngine()
                self.engine.raw = {p: native(self.engine.symbols[p[0]], value) for p in self.engine.places}
                src = SOURCE.replace('value = -1;', 'value = '+str(value)+';')
                self.assertTrue(self.run_rule(src))

    def test_missing_out_of_order_or_duplicate_primary_blocks_reference_access(self):
        primary = self.engine.primary
        for places in ([], [self.engine.places[1]], list(reversed(self.engine.places)),
                       [primary, primary], [primary, self.engine.places[1], self.engine.places[1]]):
            with self.subTest(places=places):
                self.engine = FakeEngine()
                self.engine.places = places
                self.assertEqual(self.run_rule(), [])
                self.assertFalse(any(e[0] in ('reference', 'symbol', 'raw') for e in self.engine.events))

    def test_malformed_placement_blocks_reference_access(self):
        for bad in (('level_02', '.text', True, 32), ('level_02', '.text', 0x3001, 32),
                    ('level_02', '.text', 0x3000, 31), ('level_02', '.text', 0x3000, 0),
                    ('level_02', '.text', -4, 32), ('level_02', '.text', 0xfffffff0, 32),
                    ('', '.text', 0x3000, 32), ('level_02', None, 0x3000, 32),
                    ('level_02', '.text', 0x3000, 32, 'extra')):
            with self.subTest(bad=bad):
                self.engine = FakeEngine()
                self.engine.places[-1] = bad
                self.assertEqual(self.run_rule(), [])
                self.assertFalse(any(e[0] in ('reference', 'symbol', 'raw') for e in self.engine.events))

    def test_reference_exception_or_non_none_blocks_symbol_access(self):
        for error in (ValueError('hash mismatch'), OSError('missing reference')):
            with self.subTest(error=error):
                self.engine = FakeEngine()
                self.engine.reference_error = error
                self.assertEqual(self.run_rule(), [])
                self.assertFalse(any(e[0] in ('symbol', 'raw') for e in self.engine.events))
        self.engine = FakeEngine()
        self.engine.reference_return = False
        self.assertEqual(self.run_rule(), [])
        self.assertFalse(any(e[0] in ('symbol', 'raw') for e in self.engine.events))

    def test_unresolved_or_misaligned_symbol_blocks_raw_access(self):
        for address in (None, True, -4, 0x4001, 0x100000000, '0x4000'):
            with self.subTest(address=address):
                self.engine = FakeEngine()
                self.engine.symbols['level_02'] = address
                self.assertEqual(self.run_rule(), [])
                self.assertFalse(any(e[0] == 'raw' for e in self.engine.events))

    def test_wrong_raw_type_or_declared_length_rejects(self):
        for raw in ('bad', b'', native(0x4000)[:-4], native(0x4000)+b'\x00'*4):
            with self.subTest(raw=raw):
                self.engine = FakeEngine()
                self.engine.raw[self.engine.places[1]] = raw
                self.assertEqual(self.run_rule(), [])

    def test_one_native_target_disagreement_rejects_all(self):
        self.engine.raw[self.engine.places[1]] = native(0x4004)
        self.assertEqual(self.run_rule(), [])

    def test_call_delay_slot_or_prior_immediate_rejects(self):
        for index in (0, 3):
            with self.subTest(index=index):
                self.engine = FakeEngine()
                self.engine.raw[self.engine.places[1]] = patch(native(0x4000), index, 0x2406ffff)
                self.assertEqual(self.run_rule(), [])

    def test_missing_duplicate_or_different_postcall_immediate_rejects(self):
        for raw in (patch(native(0x4000), 4, 0), patch(native(0x4000), 4, 0x24060001),
                    patch(native(0x4000), 5, 0x2407ffff)):
            with self.subTest(raw=raw):
                self.engine = FakeEngine()
                self.engine.raw[self.engine.places[1]] = raw
                self.assertEqual(self.run_rule(), [])

    def test_native_cfg_fragment_control_in_delay_slot_and_preceding_call_rejects(self):
        for raw in (patch(native(0x4000), 6, 0), patch(native(0x4000), 3, 0x03e00008),
                    patch(native(0x4000), 0, 0x0c001000)):
            with self.subTest(raw=raw):
                self.engine = FakeEngine()
                self.engine.raw[self.engine.places[1]] = raw
                self.assertEqual(self.run_rule(), [])

    def test_no_placements_no_rule_and_existing_source_not_repeated(self):
        self.assertIsNone(generate(self.rules, SOURCE, []))
        out = self.run_rule()
        self.assertEqual(self.run_rule(out[0][1]), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
