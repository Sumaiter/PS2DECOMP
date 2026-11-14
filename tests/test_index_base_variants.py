"""Guard against eager evaluation, hidden qualifiers and invented array types."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from index_base_variants import generate
from search_rules import Variants

ROOT = Path(__file__).parent / 'fixtures/project'
SOURCE = '''#include "common.h"
extern s32 select_index;
extern s32 ready(void *);
s32 f(u8 *state) {
 if (ready(state)) {
  if (*(s32 *)(state + 16) >= ((s32 *)(state + 4))[select_index]) return 1;
 }
 return 0;
}
'''


class IndexBaseTests(unittest.TestCase):
    def gen(self, source=SOURCE, root=ROOT):
        return list(generate(source, root))

    def test_preserves_signature_type_and_guard_scope(self):
        variants = self.gen()
        self.assertEqual(len(variants), 1)
        rule, output = variants[0]
        self.assertEqual(rule, 'materialize-index-base')
        self.assertIn('s32 *ps2decomp_index_base_0;', output)
        self.assertIn('ps2decomp_index_base_0[select_index]', output)
        rules = Variants(ROOT)
        old_fn, new_fn = rules.parse(SOURCE)[1], rules.parse(output)[1]
        self.assertEqual(rules.generator.visit(old_fn.decl), rules.generator.visit(new_fn.decl))
        gate = new_fn.body.block_items[1]
        self.assertIsInstance(gate, rules.a.If)
        self.assertIsInstance(gate.iftrue.block_items[0], rules.a.Assignment)
        self.assertIsInstance(gate.iftrue.block_items[1], rules.a.If)
        self.assertNotIn('[5]', output)

    def test_guard_remains_inside_loop(self):
        out = self.gen(SOURCE.replace('if (ready(state))', 'while (ready(state))'))[0][1]
        rules = Variants(ROOT)
        loop = rules.parse(out)[1].body.block_items[1]
        self.assertIsInstance(loop, rules.a.While)
        self.assertIsInstance(loop.stmt.block_items[0], rules.a.Assignment)

    def test_short_circuit_and_ternary_are_rejected(self):
        condition = '*(s32 *)(state + 16) >= ((s32 *)(state + 4))[select_index]'
        for replacement in ('state && (' + condition + ')',
                            'state || (' + condition + ')',
                            '(state ? (' + condition + ') : 0)',
                            '0 == (state && ((s32 *)(state + 4))[select_index])'):
            with self.subTest(replacement=replacement):
                self.assertFalse(self.gen(SOURCE.replace(condition, replacement)))

    def test_side_effects_and_indirect_base_are_rejected(self):
        for old, new in [('state + 4', 'get() + 4'), ('state + 4', 'state++ + 4'),
                         ('select_index]', 'select_index++]'),
                         ('state + 16', '(state = get()) + 16'),
                         ('state + 16', 'get() + 16'),
                         ('state + 4', '*(u8 **)state + 4')]:
            with self.subTest(new=new):
                self.assertFalse(self.gen(SOURCE.replace(old, new)))

    def test_escaped_shadowed_and_qualified_objects_are_rejected(self):
        for source in [SOURCE.replace('if (ready(state))', 'escape(&state); if (ready(state))'),
                       SOURCE.replace('return 0;', '{u8 *state;} return 0;'),
                       SOURCE.replace('s32 select_index;', 'volatile s32 select_index;'),
                       SOURCE.replace('u8 *state)', 'const u8 *state)'),
                       SOURCE.replace('if (ready(state))', 'goto done; done: if (ready(state))')]:
            with self.subTest(source=source):
                self.assertFalse(self.gen(source))

    def test_existing_cast_type_is_not_inferred_from_alias_spelling(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'include').mkdir()
            header = 'typedef unsigned char u8; typedef short s32;'
            (root / 'include/common.h').write_text(header)
            output = self.gen(root=root)[0][1]
            self.assertIn('s32 *ps2decomp_index_base_0;', output)
            self.assertNotIn('int *ps2decomp_index_base_0;', output)
            (root / 'include/common.h').write_text('typedef unsigned char u8; typedef volatile int s32;')
            self.assertFalse(self.gen(root=root))
            (root / 'include/common.h').write_text(header + '\n#define select_index side_effect()\n')
            self.assertFalse(self.gen(root=root))

    def test_float_cast_and_name_collision(self):
        output = self.gen(SOURCE.replace('extern s32 ready', 'extern s32 ps2decomp_index_base_0;\nextern s32 ready').replace('(s32 *)', '(f32 *)'))[0][1]
        self.assertIn('f32 *ps2decomp_index_base_1;', output)

    def test_multiple_arrays_and_unbraced_insertion_are_rejected(self):
        self.assertFalse(self.gen(SOURCE.replace('*(s32 *)(state + 16)', '((s32 *)(state + 16))[select_index]')))
        self.assertFalse(self.gen(SOURCE.replace('if (ready(state)) {', 'if (ready(state))').replace('\n }\n', '\n')))

    def test_context_macros_and_repeated_generation(self):
        self.assertFalse(self.gen(SOURCE.replace('return 0;', 'return __LINE__;')))
        output = self.gen()[0][1]
        self.assertFalse(self.gen(output))

    def test_static_global_and_attributed_bases_are_rejected(self):
        self.assertFalse(self.gen(SOURCE.replace('s32 f(u8 *state)', 'u8 *state; s32 f(void)')))
        self.assertFalse(self.gen(SOURCE.replace('s32 f(u8 *state)', 's32 f(void)').replace(' if (ready', ' static u8 *state;\n if (ready')))
        self.assertFalse(self.gen(SOURCE.replace('u8 *state)', 'u8 *state __attribute__((unused)))')))


if __name__ == '__main__':
    unittest.main()
