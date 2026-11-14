from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comparison_value_variants import generate
from search_rules import Variants

ROOT = Path(__file__).parent / 'fixtures/project'
SOURCE = '''#include "common.h"
s64 f(u8 *p) {
 s64 result;
 result = 0;
 if (p[4] == 3) { result = p[8] == 7; }
 return result;
}
'''


class ComparisonValueTests(unittest.TestCase):
    def gen(self, source=SOURCE, root=ROOT):
        return list(generate(source, root))

    def test_keeps_signature_destination_and_conditional_access(self):
        label, output = self.gen()[0]
        self.assertEqual(label, 'materialize-comparison-value')
        self.assertIn('int ps2decomp_comparison_0;', output)
        self.assertIn('s64 result;', output)
        rules = Variants(ROOT)
        before, after = rules.parse(SOURCE)[1], rules.parse(output)[1]
        self.assertEqual(rules.generator.visit(before.decl), rules.generator.visit(after.decl))
        branch = after.body.block_items[3]
        self.assertIsInstance(branch, rules.a.If)
        self.assertEqual(rules.generator.visit(branch.iftrue.block_items[0].rvalue), 'p[8] == 7')
        self.assertEqual(rules.generator.visit(branch.iftrue.block_items[1]), 'result = ps2decomp_comparison_0')
        self.assertFalse(self.gen(output))

    def test_loop_evaluation_stays_inside_loop(self):
        output = self.gen(SOURCE.replace('if (p[4] == 3)', 'while (p[4] == 3)'))[0][1]
        rules = Variants(ROOT)
        loop = rules.parse(output)[1].body.block_items[3]
        self.assertIsInstance(loop, rules.a.While)
        self.assertEqual(len(loop.stmt.block_items), 2)

    def test_does_not_invent_or_infer_destination_width(self):
        for spelling in ['int', 'short', 'unsigned char', 'unsigned long long']:
            with self.subTest(spelling=spelling):
                self.assertTrue(self.gen(SOURCE.replace('s64', spelling)))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'include').mkdir()
            header = root / 'include/common.h'
            header.write_text('typedef unsigned char u8; typedef short s64;')
            self.assertTrue(self.gen(root=root))
            header.write_text('typedef unsigned char u8; typedef volatile int s64;')
            self.assertFalse(self.gen(root=root))

    def test_no_eager_short_circuit_ternary_or_side_effects(self):
        for expr in ['p && p[8] == 7', 'p ? p[8] == 7 : 0',
                     'get() == 7', 'p[8]++ == 7', '(result = 1) == 7']:
            with self.subTest(expr=expr):
                self.assertFalse(self.gen(SOURCE.replace('p[8] == 7', expr)))

    def test_local_unescaped_unambiguous_destination_only(self):
        for source in [SOURCE.replace('s64 result;', '' ).replace('s64 f', 's64 result; s64 f'),
                       SOURCE.replace('s64 result;', 'static s64 result;'),
                       SOURCE.replace('result = 0;', 'escape(&result); result = 0;'),
                       SOURCE.replace('return result;', '{s64 result;} return result;'),
                       SOURCE.replace('s64 result;', 'volatile s64 result;'),
                       SOURCE.replace('result = p[8]', '*p = p[8]'),
                       SOURCE.replace('result = p[8]', 'result += p[8]')]:
            with self.subTest(source=source):
                self.assertFalse(self.gen(source))

    def test_unknown_type_local_typedef_labels_and_macros(self):
        for source in [SOURCE.replace('s64 result;', 'struct X result;'),
                       SOURCE.replace('s64 result;', 'typedef int T; T result;'),
                       SOURCE.replace('return result;', 'goto end; end: return result;'),
                       SOURCE.replace('return result;', 'return __LINE__;'),
                       '#define p anything\n' + SOURCE,
                       SOURCE.replace('u8 *p', 'const u8 *p')]:
            with self.subTest(source=source):
                self.assertFalse(self.gen(source))

    def test_unbraced_or_value_assignments_are_not_split(self):
        self.assertFalse(self.gen(SOURCE.replace('{ result = p[8] == 7; }', 'result = p[8] == 7;')))
        self.assertFalse(self.gen(SOURCE.replace('result = p[8] == 7;', 'if ((result = p[8] == 7)) return 1;')))

    def test_reserved_enum_name_and_float_comparison(self):
        source = SOURCE.replace('s64 result;', 'enum {ps2decomp_comparison_0}; s64 result;')
        self.assertIn('int ps2decomp_comparison_1;', self.gen(source)[0][1])
        self.assertTrue(self.gen(SOURCE.replace('p[8] == 7', '*(float *)p > 0.5f')))


if __name__ == '__main__':
    unittest.main()
