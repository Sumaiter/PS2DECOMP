import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).parent / 'fixtures' / 'project'
ADDON = Path(__file__).resolve().parents[1]
sys.path.append(str(Path(__file__).resolve().parents[1]))
from search_rules import Variants
import disjoint_store_swap as rule

HEADER = (ROOT / 'include/common.h').read_text(encoding='utf-8')
BASE = 'void f(void *p,s32 v){*(s32 *)((s8 *)p+0)=v;*(s32 *)((s8 *)p+4)=0;}'


class StoreSwapTests(unittest.TestCase):
    def setUp(self):
        self.rules = Variants(ROOT)

    def generate(self, source, rules=None, header=HEADER, bits=32):
        if '#include "common.h"' not in source:
            source = '#include "common.h"\n' + source
        return rule.generate(source, rules or self.rules, common_header=header, address_bits=bits)

    def canonical(self, source):
        return self.rules.generator.visit(self.rules.parse(source)[0])

    def test_positive_bound_one_and_only_statement_swap(self):
        sources = [BASE,
            BASE.replace('(s32 *)((s8 *)p+0)', '(u8 *)((s8 *)p+0)'),
            'void f(void *p,void *v){*(void **)((s8 *)p+0)=v;*(void **)((s8 *)p+4)=NULL;}',
            'void f(void *p,u32 v){*(u32 *)((u8 *)p+0)=v;*(u32 *)((u8 *)p+4)=0U;}',
            'void f(void *p){*(u8 *)((char *)p+0)=255;*(s16 *)((char *)p+2)=1;}',
            'void f(void *q,s32 v){void *p;p=q;*(s32 *)((s8 *)p+4)=v;*(s32 *)((s8 *)p+0)=0;}',
        ]
        for source in sources:
            with self.subTest(source=source):
                tree, fn = self.rules.parse(source)
                before = copy.deepcopy(tree)
                items = fn.body.block_items
                items[-2], items[-1] = items[-1], items[-2]
                wanted = self.rules.generator.visit(tree)
                output = self.generate(source)
                self.assertIsNotNone(output)
                self.assertEqual(self.canonical(output), wanted)
                self.assertNotEqual(self.rules.generator.visit(before), wanted)
                self.assertEqual(len(rule.children('#include "common.h"\n' + source, self.rules, address_bits=32, common_header=HEADER)), 1)
        many = BASE[:-1] + '*(s32 *)((s8 *)p+8)=v;*(s32 *)((s8 *)p+12)=0;}'
        self.assertEqual(len(rule.children('#include "common.h"\n' + many, self.rules, address_bits=32, common_header=HEADER)), 1)
        self.assertIsNone(rule.generate(BASE, self.rules, address_bits=32, common_header=HEADER))

    def test_counterexamples(self):
        cases = {
            'overlap': BASE.replace('p+4', 'p+2'),
            'same-range': BASE.replace('p+4', 'p+0'),
            'different-base': BASE.replace('s32 v)', 's32 v,void *q)').replace('p+4', 'q+4'),
            'rhs-load': BASE.replace('=0;', '=*(s32 *)p;'),
            'rhs-call': 's32 g(void);' + BASE.replace('=0;', '=g();'),
            'rhs-increment': BASE.replace('=v;', '=v++;'),
            'rhs-overflow-expression': BASE.replace('=v;', '=v+1;'),
            'rhs-float': BASE.replace('=0;', '=0.0f;'),
            'rhs-unknown-id': BASE.replace('=0;', '=SOME_MACRO;'),
            'rhs-volatile': BASE.replace('s32 v)', 'volatile s32 v)'),
            'rhs-static': BASE.replace('s32 v)', 's32 x)').replace('{*', '{static s32 v;*', 1),
            'rhs-extern': 'extern s32 v;' + BASE.replace(',s32 v', ''),
            'rhs-global': 's32 v;' + BASE.replace(',s32 v', ''),
            'global-shadow': 's32 v;' + BASE,
            'local-shadow': BASE.replace('{*', '{s32 v;*', 1),
            'nested-shadow': BASE[:-1] + '{s32 v;}}',
            'NULL-variable': 'void f(void *p,void *NULL){*(void **)((s8 *)p+0)=NULL;*(void **)((s8 *)p+4)=0;}',
            'escaping-rhs': BASE.replace('{*', '{s32 *q;q=&v;*', 1),
            'escaping-base': BASE.replace('{*', '{void **q;q=&p;*', 1),
            'static-base': 'void f(s32 v){static void *p;*(s32 *)((s8 *)p+0)=v;*(s32 *)((s8 *)p+4)=0;}',
            'integer-base': BASE.replace('void *p', 'u32 p'),
            'pointer-pointer-base': BASE.replace('void *p', 'void **p'),
            'aggregate-pointer-base': 'struct S;' + BASE.replace('void *p', 'struct S *p'),
            'array-base': BASE.replace('void *p', 's8 p[8]'),
            'const-base': BASE.replace('void *p', 'void *const p'),
            'qualified-pointee': BASE.replace('(s32 *)', '(const s32 *)', 1),
            'float-store': BASE.replace('(s32 *)', '(f32 *)', 1),
            'composite-store': BASE.replace('(s32 *)', '(s32 ***)', 1),
            'dynamic-offset': BASE.replace('p+4', 'p+v'),
            'offset-expression': BASE.replace('p+4', 'p+(2+2)'),
            'offset-overflow': BASE.replace('p+4', 'p+0x7fffffff'),
            'macro-definition': '#define v 3\n' + BASE,
            'other-include': '#include "other.h"\n' + BASE,
            'source-typedef': 'typedef unsigned long s32;' + BASE,
            'builtin': BASE.replace('=v;', '=__builtin_abs(v);'),
            'asm': BASE.replace('{*', '{asm("nop");*', 1),
            'bad-cflags-spelling': '/*cflags: -O3 */\n' + BASE,
            'bad-cflags-position': '\n\n\n/* cflags: -O3 */\n' + BASE,
            'unsupported-cflags': '/* cflags: -fvolatile */\n' + BASE,
            'pointer-nonzero-constant': 'void f(void *p){*(void **)((s8 *)p+0)=1;*(void **)((s8 *)p+4)=0;}',
            'pointer-integer-value': 'void f(void *p,s32 v){*(void **)((s8 *)p+0)=v;*(void **)((s8 *)p+4)=0;}',
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                self.assertIsNone(self.generate(source))

    def test_real_typedefs_header_and_abi_fail_closed(self):
        for bits in (None, 64, 16, True, '32'):
            with self.subTest(bits=bits):
                self.assertIsNone(self.generate(BASE, bits=bits))
        for replacement in ('unsigned long u32', 'const unsigned int u32',
                            'volatile unsigned int u32', 'unsigned short u32',
                            'u32 u32', 'unknown u32'):
            rules = Variants(ROOT)
            header = HEADER.replace('unsigned int u32', replacement)
            rules.prelude = '\n'.join(line for line in header.splitlines() if line.strip().startswith('typedef '))
            with self.subTest(replacement=replacement):
                self.assertIsNone(self.generate(BASE, rules, header))
        for header in (None, HEADER.replace('#define NULL ((void *)0)', '#define NULL side_effect()'),
                       HEADER.replace('#define NULL ((void *)0)', '#define NULL ((void *)0)\n#define v foo'),
                       HEADER + '\nextern int v;', HEADER.replace('unsigned int u32', 'unsigned long u32')):
            with self.subTest(header=header):
                self.assertIsNone(self.generate(BASE, header=header))

    def test_cached_ast_not_mutated(self):
        parsed = self.rules.parse(BASE)
        before = self.rules.generator.visit(parsed[0])
        self.rules.parse = lambda _: parsed
        self.assertIsNotNone(self.generate(BASE))
        self.assertEqual(self.rules.generator.visit(parsed[0]), before)

    def test_flags_and_untouched_fp_and_arithmetic(self):
        source = '/* cflags: -fno-schedule-insns */\n' + BASE.replace('{*', '{f32 fp;f32 g;s32 z;fp=fp/g;z=v+1;*', 1)
        output = self.generate(source)
        self.assertIsNotNone(output)
        self.assertEqual(output.splitlines()[0], '/* cflags: -fno-schedule-insns */')
        before, fn = self.rules.parse(source)
        fn.body.block_items[-2:] = fn.body.block_items[-2:][::-1]
        self.assertEqual(self.canonical(output), self.rules.generator.visit(before))

    @unittest.skip("Original game/compiler review evidence stays in the originating local repository.")
    def test_retained_real_compiler_pair_exact_ast(self):
        proposal = json.loads((ROOT / 'build/review_auto19/proposalalgorithm_b/proposal.json').read_text(encoding='utf-8'))
        source = Path(proposal['compilerPair'][0]['snapshot']).read_text(encoding='utf-8')
        expected = Path(proposal['compilerPair'][1]['snapshot']).read_text(encoding='utf-8')
        output = self.generate(source)
        self.assertIsNotNone(output)
        self.assertEqual(self.canonical(output), self.canonical(expected))


if __name__ == '__main__':
    unittest.main(verbosity=2)
