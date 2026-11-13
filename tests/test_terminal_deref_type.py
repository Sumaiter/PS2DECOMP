"""Production return-join guards; no compiler, native code or user session touched."""
from pathlib import Path
import unittest
from search_rules import Variants
RUNTIME=(Path(__file__).parent / 'fixtures/project')

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = Variants(RUNTIME)

    def joined(self, source):
        return [text for rule, text in self.rules.generate(source, limit=200)
                if rule == "terminale-rueckgaben-verbinden"]

    def test_explicit_s32_load_and_guarded_getter_call(self):
        src = '#include "common.h"\nu8 *get(void);s32 f(u32 i){if(i>=4U)return 0;return *(s32*)(get()+i*4+448);}'
        output = self.joined(src)
        self.assertEqual(len(output), 1)
        tree, fn = self.rules.parse(output[0])
        guard = next(n for n in fn.body.block_items if isinstance(n, self.rules.a.If))
        self.assertFalse(any(isinstance(n, self.rules.a.FuncCall) for *_, n in self.rules._nodes(guard.cond)))
        self.assertEqual(sum(isinstance(n, self.rules.a.FuncCall) for *_, n in self.rules._nodes(guard.iftrue)), 0)
        self.assertEqual(sum(isinstance(n, self.rules.a.FuncCall) for *_, n in self.rules._nodes(guard.iffalse)), 1)
        self.assertEqual(sum(isinstance(n, self.rules.a.UnaryOp) and n.op == '*' for *_, n in self.rules._nodes(fn)), 1)

    def test_qualified_pointees_stay_manual(self):
        for qual in ("volatile", "const", "const volatile"):
            with self.subTest(qual=qual):
                self.assertEqual(self.joined(f's32 f(s32 *p,s32 i){{if(i)return 0;return *({qual} s32*)p;}}'), [])

    def test_pointer_qualifier_stays_manual(self):
        self.assertEqual(self.joined('s32 f(s32 *p,s32 i){if(i)return 0;return *(s32 * const)p;}'), [])

    def test_implicit_pointer_type_stays_manual(self):
        self.assertEqual(self.joined('s32 f(s32 *p,s32 i){if(i)return 0;return *p;}'), [])

    def test_pointer_to_pointer_stays_manual(self):
        self.assertEqual(self.joined('s32 f(s32 **p,s32 i){if(i)return 0;return *(s32**)p;}'), [])

    def test_return_type_mismatch_stays_manual(self):
        self.assertEqual(self.joined('u32 f(s32 *p,s32 i){if(i)return 0U;return *(s32*)p;}'), [])

    def test_source_alias_and_hidden_qualifier_stay_manual(self):
        for typ in ('s32', 'volatile s32'):
            self.assertEqual(self.joined('typedef '+typ+' Alias;s32 f(s32 *p,s32 i){if(i)return 0;return *(Alias*)p;}'), [])

    def test_address_mutations_stay_manual(self):
        for expr in ('p++', '--p', '(p=q)', '(i++,p)'):
            with self.subTest(expr=expr):
                self.assertEqual(self.joined('s32 f(s32 *p,s32 *q,s32 i){if(i)return 0;return *(s32*)('+expr+');}'), [])

    def test_nonterminal_and_extra_guard_effect_stay_manual(self):
        self.assertEqual(self.joined('s32 f(s32 *p,s32 i){if(i){*p=1;return 0;}return *(s32*)p;}'), [])
        self.assertEqual(self.joined('s32 f(s32 *p,s32 i){if(i)return 0;*p=1;return *(s32*)p;}'), [])

    def test_label_stays_manual(self):
        self.assertEqual(self.joined('s32 f(s32 *p,s32 i){label:if(i)return 0;return *(s32*)p;}'), [])

if __name__ == '__main__':
    unittest.main()
