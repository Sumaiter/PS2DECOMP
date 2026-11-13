"""Synthetic instructions only; local retail/compiler evidence stays private."""
from pathlib import Path
import struct
import tempfile
import unittest

from RAC3_PAL.quadword_zero import children
from search_rules import Variants


HEADER = '''#ifndef COMMON_H
#define COMMON_H
typedef signed char s8;
typedef unsigned char u8;
typedef signed int s32;
typedef unsigned int u32;
#endif /* COMMON_H */
'''
HELPER = '''#ifndef VU0_H
#define VU0_H
typedef int vu0_q __attribute__((mode(TI)));
#define qzero(d) __asm__("sq $0, %0" : "=m"(*(vu0_q *)(d)))
#endif /* VU0_H */
'''


def i(op, base, value, offset):
    return op << 26 | base << 21 | value << 16 | (offset & 0xffff)


def blob(*words):
    return struct.pack('<' + 'I' * len(words), *words)


def copy_clear_native(source_offset=0x30, zero_offset=0x20, word_offset=0x44, output=0):
    return blob(i(30, 4, 2, source_offset), i(43, 5, 0, word_offset),
                i(31, 5, 0, zero_offset), 0x03e00008, i(31, 5, 2, output))


COPY_SOURCE = '''#include "common.h"
typedef long long item128;
void func_00100000(void *source, void *output) {
    *(s32 *)((s8 *)output + 0x44) = 0;
    *(item128 *)((s8 *)output + 0x20) = 0;
    *(item128 *)((s8 *)output + 0) = (item128)*(item128 *)((s8 *)source + 0x30);
}
'''


class FakeEngine:
    version = 'pal'

    def __init__(self, root, raws):
        self.root, self.raws, self.checked = root, raws, []
        self.owner_paths = {'func_00100000': ['src/levels/shared/unit.c']}

    def all_places(self, name):
        return [(str(n), '.text', 0x100000 + n * 0x10000, len(raw)) for n, raw in enumerate(self.raws)]

    def assert_reference(self, program):
        self.checked.append(program)

    def disc_bytes(self, place):
        return self.raws[int(place[0])]


class QuadwordZeroTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'include').mkdir()
        (self.root / 'include/common.h').write_text(HEADER, encoding='utf-8')
        (self.root / 'include/vu0.h').write_text(HELPER, encoding='utf-8')
        (self.root / 'config/levels').mkdir(parents=True)
        (self.root / 'config/levels/text_parts.txt').write_text(
            'src/levels/shared/unit.c\t-O2 -G8 -fopt-stack -mno-check-zero-division\n', encoding='utf-8')
        (self.root / 'src/levels/shared').mkdir(parents=True)
        (self.root / 'src/levels/shared/unit.c').write_text('#include "common.h"\n', encoding='utf-8')
        self.rules = Variants(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def generate(self, source=COPY_SOURCE, *raws):
        self.engine = FakeEngine(self.root, raws or [copy_clear_native()])
        return children(self.engine, 'func_00100000', source, self.rules)

    def test_existing_helper_and_native_copy_before_clear(self):
        rows = self.generate(COPY_SOURCE, copy_clear_native(), copy_clear_native())
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.engine.checked, ['0', '1'])
        first, repaired = [text for _, text in rows]
        self.assertIn('#include "vu0.h"', first)
        self.assertEqual(first.count('qzero('), 1)
        self.assertIn('item128 rac3_quadword_value_0', repaired)
        self.assertLess(repaired.index('rac3_quadword_value_0 ='), repaired.index('0x44'))
        self.assertNotIn('__asm__', repaired)
        self.assertIn('void func_00100000(void *source, void *output)', repaired)

    def test_copy_recovery_follows_offsets_instead_of_a_function_table(self):
        source = COPY_SOURCE.replace('0x30', '0x50').replace('0x44', '0x64').replace('0x20', '0x40')
        self.assertEqual(len(self.generate(source, copy_clear_native(0x50, 0x40, 0x64))), 2)

    def test_mismatching_other_placement_rejects_every_seed(self):
        other = copy_clear_native(zero_offset=0x40)
        self.assertEqual(self.generate(COPY_SOURCE, copy_clear_native(), other), [])

    def test_same_offset_different_actual_base_is_not_evidence(self):
        raw = blob(i(30, 4, 2, 0x30), i(43, 5, 0, 0x44), i(31, 4, 0, 0x20),
                   0x03e00008, i(31, 5, 2, 0))
        self.assertEqual(self.generate(COPY_SOURCE, raw), [])

    def test_extra_native_or_source_zero_rejects_partial_conversion(self):
        source = COPY_SOURCE.replace('    *(item128 *)((s8 *)output + 0)',
                                     '    *(item128 *)((s8 *)output + 0x60) = 0;\n    *(item128 *)((s8 *)output + 0)')
        self.assertEqual(self.generate(source), [])
        raw = blob(i(31, 5, 0, 0x60), i(30, 4, 2, 0x30), i(43, 5, 0, 0x44),
                   i(31, 5, 0, 0x20), 0x03e00008, i(31, 5, 2, 0))
        self.assertEqual(self.generate(COPY_SOURCE, raw), [])

    def test_64_bit_nonzero_qualified_and_attributed_types_are_not_guessed(self):
        for source in [COPY_SOURCE.replace('long long', 'long'),
                       COPY_SOURCE.replace(' + 0x20) = 0;', ' + 0x20) = 1;'),
                       COPY_SOURCE.replace('typedef long long', 'typedef volatile long long'),
                       COPY_SOURCE.replace('typedef long long item128;',
                                           'typedef long long item128 __attribute__((mode(DI)));')]:
            with self.subTest(source=source):
                self.assertEqual(self.generate(source), [])

    def test_function_local_typedef_shadow_cannot_change_the_store_width(self):
        source = COPY_SOURCE.replace('    *(s32 *)', '    typedef long item128;\n    *(s32 *)', 1)
        self.assertEqual(self.generate(source), [])

    def test_macro_and_conditional_header_types_are_not_ignored(self):
        for changed in [HEADER.replace('#endif', '#define item128 long\n#endif'),
                        HEADER.replace('typedef signed int s32;', '#if 0\ntypedef signed int s32;\n#endif'),
                        HEADER.replace('typedef signed int s32;', 'typedef long s32;')]:
            with self.subTest(header=changed):
                (self.root / 'include/common.h').write_text(changed, encoding='utf-8')
                self.assertEqual(self.generate(), [])

    def test_plain_include_asm_context_is_supported_but_type_macros_are_not(self):
        (self.root / 'include/common.h').write_text(HEADER.replace('#endif', '#include "include_asm.h"\n#endif'), encoding='utf-8')
        (self.root / 'include/include_asm.h').write_text('#define INCLUDE_ASM_H\n#define INCLUDE_ASM(a,b)\n', encoding='utf-8')
        self.rules = Variants(self.root)
        self.assertEqual(len(self.generate()), 2)
        (self.root / 'include/include_asm.h').write_text('#define INCLUDE_ASM_H\n#define item128 long\n', encoding='utf-8')
        self.assertEqual(self.generate(), [])

    def test_ee29_or_unproved_abi_flags_are_rejected_for_the_actual_owner(self):
        path = self.root / 'config/levels/text_parts.txt'
        for flags in ['-O2 -G8 @ee29', '-O2 -G8 -mint64', '-O2 -G8 -mlong32', '']:
            with self.subTest(flags=flags):
                path.write_text('src/levels/shared/unit.c\t'+flags+'\n', encoding='utf-8')
                self.assertEqual(self.generate(), [])

    def test_owner_context_cannot_shadow_source_ti_aliases_or_primitive_tokens(self):
        path = self.root / 'src/levels/shared/unit.c'
        for context in ['typedef long item128;', '#define long int', '#if 0\ntypedef long item128;\n#endif',
                        '#include "unparsed_types.h"']:
            with self.subTest(context=context):
                path.write_text('#include "common.h"\n'+context+'\n', encoding='utf-8')
                self.assertEqual(self.generate(), [])

    def test_common_byte_type_attributes_cannot_be_lost_during_address_analysis(self):
        (self.root / 'include/common.h').write_text(HEADER.replace('typedef signed char s8;',
            'typedef signed char s8 __attribute__((mode(DI)));'), encoding='utf-8')
        self.rules = Variants(self.root)
        self.assertEqual(self.generate(), [])

    def test_r5900_mult_gpr_result_is_not_treated_as_a_preserved_pointer(self):
        # MULT rd=$a1 destroys the only proved output base before the zero.
        multiply = 4 << 21 | 6 << 16 | 5 << 11 | 24
        words = struct.unpack('<5I', copy_clear_native())
        raw = blob(words[0], multiply, *words[1:])
        self.assertEqual(self.generate(COPY_SOURCE, raw), [])

    def test_jal_link_register_is_not_a_live_output_pointer_in_its_slot(self):
        move_to_ra = 5 << 21 | 31 << 11 | 45
        raw = blob(move_to_ra, 3 << 26 | 0x100, i(31, 31, 0, 0x20),
                   0x03e00008, 0)
        self.assertEqual(self.generate(COPY_SOURCE, raw), [])

    def test_owner_profile_is_unique_and_exactly_resolves_this_source(self):
        engine = FakeEngine(self.root, [copy_clear_native()])
        for owners in [[], ['src/core/unit.c'], ['src/levels/a.c', 'src/levels/b.c']]:
            with self.subTest(owners=owners):
                engine.owner_paths['func_00100000'] = owners
                self.assertEqual(children(engine, 'func_00100000', COPY_SOURCE, self.rules), [])
        row = 'src/levels/shared/unit.c\t-O2 -G8\n'
        (self.root / 'config/levels/text_parts.txt').write_text(row+row, encoding='utf-8')
        self.assertEqual(self.generate(), [])

    def test_changed_helper_width_body_and_conditional_are_rejected(self):
        for helper in [HELPER.replace('mode(TI)', 'mode(DI)'),
                       HELPER.replace('sq $0', 'sd $0'),
                       HELPER.replace('#define qzero', '#define TI DI\n#define qzero'),
                       HELPER.replace('#define qzero', '#define mode(x) mode(DI)\n#define qzero'),
                       HELPER.replace('#define qzero', '#define vu0_q long\n#define qzero'),
                       HELPER.replace('#define qzero', '#include "override.h"\n#define qzero'),
                       HELPER.replace('#define qzero', '#undef TI\n#define qzero'),
                       HELPER.replace('#define qzero', '#if 0\n#define qzero').replace('#endif', '#endif\n#endif')]:
            with self.subTest(helper=helper):
                (self.root / 'include/vu0.h').write_text(helper, encoding='utf-8')
                self.assertEqual(self.generate(), [])

    def test_unknown_mutated_or_address_escaped_pointer_is_not_a_base(self):
        for source in [COPY_SOURCE.replace('    *(s32 *)', '    output = source;\n    *(s32 *)', 1),
                       COPY_SOURCE.replace('    *(s32 *)', '    void **escape = &output;\n    *(s32 *)', 1),
                       COPY_SOURCE.replace('(s8 *)output + 0x20', '(s8 *)unknown + 0x20')]:
            with self.subTest(source=source):
                self.assertEqual(self.generate(source), [])

    def test_global_initializers_and_source_context_builtins_are_not_regenerated(self):
        for declaration in ['int extra = 3;\n', 'int extra;\n', 'enum { site = __LINE__ };\n']:
            with self.subTest(declaration=declaration):
                self.assertEqual(self.generate(COPY_SOURCE.replace('typedef long long', declaration+'typedef long long')), [])

    def test_alignment_and_delay_slot_zero_are_not_invented(self):
        source = COPY_SOURCE.replace(' + 0x20', ' + 0x28')
        self.assertEqual(self.generate(source, copy_clear_native(zero_offset=0x28)), [])
        raw = blob(i(30, 4, 2, 0x30), i(43, 5, 0, 0x44), i(31, 5, 2, 0),
                   0x03e00008, i(31, 5, 0, 0x20))
        self.assertEqual(self.generate(COPY_SOURCE, raw), [])

    def test_repair_rejects_a_different_native_load_or_word_clear(self):
        for raw in [copy_clear_native(source_offset=0x50), copy_clear_native(word_offset=0x48)]:
            rows = self.generate(COPY_SOURCE, raw)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], 'rac3-quadword-zero-helper')

    def test_runtime_alias_counterexample_needs_the_saved_load(self):
        initial = bytearray(range(128))
        # source+0x30 and output+0x20 can be the same range: output=source+0x10.
        source, output = 0, 0x10
        before = bytes(initial[source+0x30:source+0x40])
        def evaluate(saved):
            memory = bytearray(initial)
            value = bytes(memory[source+0x30:source+0x40]) if saved else None
            memory[output+0x44:output+0x48] = bytes(4)
            memory[output+0x20:output+0x30] = bytes(16)
            value = value if saved else bytes(memory[source+0x30:source+0x40])
            memory[output:output+16] = value
            return bytes(memory[output:output+16])
        self.assertEqual(evaluate(True), before)
        self.assertEqual(evaluate(False), bytes(16))

    def test_all_three_loaded_pointer_sites_survive_calls_without_reordering(self):
        source = '''#include "common.h"
typedef long long item128;
void called(void *a);
void func_00100000(void *object) {
    void *values;
    values = *(void **)((s8 *)object + 0x38);
    called(object);
    *(item128 *)((s8 *)values + 0x20) = 0;
    *(s32 *)((s8 *)values + 0x44) = 7;
    *(item128 *)((s8 *)values + 0x40) = 0;
    *(item128 *)((s8 *)values + 0x60) = 0;
}'''
        raw = blob(i(9, 29, 29, -16), i(63, 29, 31, 0), i(35, 4, 16, 0x38),
                   3 << 26 | 0x100, 0, i(31, 16, 0, 0x20), i(9, 0, 2, 7),
                   i(43, 16, 2, 0x44), i(31, 16, 0, 0x40), i(31, 16, 0, 0x60),
                   i(55, 29, 31, 0), 0x03e00008, i(9, 29, 29, 16))
        rows = self.generate(source, raw)
        self.assertEqual(len(rows), 1)
        text = rows[0][1]
        self.assertEqual(text.count('qzero('), 3)
        self.assertLess(text.index('0x20'), text.index('0x44'))
        self.assertLess(text.index('0x44'), text.index('0x40'))
        # A scalar load from another object's identical offset is not the pointer.
        wrong = raw.replace(struct.pack('<I', i(35, 4, 16, 0x38)), struct.pack('<I', i(35, 5, 16, 0x38)))
        self.assertEqual(self.generate(source, wrong), [])

    def test_pointer_load_cannot_silently_cross_a_memory_write(self):
        source = '''#include "common.h"
typedef long long item128;
void func_00100000(void *object) {
    void *values;
    values = *(void **)((s8 *)object + 0x38);
    *(s32 *)((s8 *)object + 0x38) = 0;
    *(item128 *)((s8 *)values + 0x20) = 0;
}'''
        valid = blob(i(35, 4, 16, 0x38), i(43, 4, 0, 0x38), i(31, 16, 0, 0x20), 0x03e00008, 0)
        invalid = blob(i(43, 4, 0, 0x38), i(35, 4, 16, 0x38), i(31, 16, 0, 0x20), 0x03e00008, 0)
        self.assertEqual(len(self.generate(source, valid)), 1)
        self.assertEqual(self.generate(source, invalid), [])

    def test_unknown_reference_region_and_non_function_boundary_fail_closed(self):
        self.engine = FakeEngine(self.root, [copy_clear_native()])
        self.engine.version = 'us'
        self.assertEqual(children(self.engine, 'func_00100000', COPY_SOURCE, self.rules), [])
        self.assertEqual(self.generate(COPY_SOURCE, copy_clear_native()[:-4]), [])
        self.assertEqual(children(FakeEngine(self.root, []), 'func_00100000', COPY_SOURCE, self.rules), [])


if __name__ == '__main__':
    unittest.main()
