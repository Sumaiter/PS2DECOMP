"""Independent, compiler-free regressions for the Review20 B proposal.

AUDIT_MODULE selects the immutable reviewed module or a proposed fixed copy.
"""
from pathlib import Path
import importlib.util
import os
import re
import sys
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
ROOT = (Path(__file__).parent / 'fixtures/project')
HERE = Path(__file__).resolve().parent
import postcall_integer_default as module

BASE = '''#include "common.h"
int callback(s32 value);
int example(s32 input) {
 s32 result;
 result = -542;
 if (callback(input) >= 0) { result = input; }
 return result;
}
'''


class IndependentGuards(unittest.TestCase):
    def generated(self, source=BASE):
        return list(module.generate(source, ROOT))

    def prelude(self, text, source=BASE):
        original = module.Variants
        class ModifiedPrelude(original):
            def __init__(self, repository):
                super().__init__(repository)
                self.prelude = text
                self.header_text = text
        with patch.object(module, 'Variants', ModifiedPrelude):
            return self.generated(source)

    def test_normal_plain_alias_and_plain_int_still_apply(self):
        self.assertEqual(len(self.generated()), 1)
        self.assertEqual(len(self.prelude('typedef int intermediate; typedef intermediate s32;')), 1)
        self.assertEqual(len(self.generated(BASE.replace('s32', 'int'))), 1)

    def test_hidden_prelude_qualifiers_rejected(self):
        for qualifier in ('volatile', 'const', '_Atomic'):
            with self.subTest(qualifier=qualifier):
                self.assertFalse(self.prelude(f'typedef {qualifier} int s32;'))

    def test_hidden_prelude_attributes_rejected(self):
        for attribute in ('mode(HI)', 'mode(QI)', 'aligned(16)', 'cleanup(clean)'):
            with self.subTest(attribute=attribute):
                self.assertFalse(self.prelude(f'typedef int s32 __attribute__(({attribute}));'))

    def test_attributes_on_intermediate_alias_rejected(self):
        self.assertFalse(self.prelude('typedef int hidden __attribute__((mode(HI))); typedef hidden s32;'))

    def test_duplicate_prelude_aliases_rejected(self):
        self.assertFalse(self.prelude('typedef volatile int s32; typedef int s32;'))
        self.assertFalse(self.prelude('typedef int other; typedef int other; typedef int s32;'))

    def test_prelude_cycle_and_unknown_or_pointer_alias_rejected(self):
        for prelude in ('typedef int s32; typedef s32 s32;',
                        'typedef int *s32;', 'typedef unsigned int s32;',
                        'typedef long s32;'):
            with self.subTest(prelude=prelude):
                self.assertFalse(self.prelude(prelude))
        # A permissive AST provider must fail closed on an actual alias cycle.
        original = module.Variants
        class CyclicPrelude(original):
            def __init__(self, repository):
                super().__init__(repository)
                actual = self.parser
                class CyclicParser:
                    def parse(inner, text):
                        tree = actual().parse(text)
                        if text == self.prelude:
                            alias = next(n for n in tree.ext if isinstance(n, self.a.Typedef) and n.name == 's32')
                            alias.type.type.names = ['s32']
                        return tree
                self.parser = CyclicParser
        with patch.object(module, 'Variants', CyclicPrelude):
            self.assertFalse(self.generated())

    def test_inner_callback_pointer_shadow_rejected(self):
        source = BASE.replace(' result = -542;\n if', ' { s64 (*callback)(s32) = wide;\n result = -542;\n if')
        source = source.replace('int example', 's64 wide(s32 value);\nint example').replace(' return result;', ' }\n return result;')
        self.assertFalse(self.generated(source))

    def test_for_scope_callback_pointer_shadow_rejected(self):
        source = BASE.replace(' result = -542;\n if', ' for (s64 (*callback)(s32) = wide; input; input = 0) {\n result = -542;\n if')
        source = source.replace('int example', 's64 wide(s32 value);\nint example').replace(' return result;', ' }\n return result;')
        self.assertFalse(self.generated(source))

    def test_private_default_and_call_arguments_remain_unobservable(self):
        for argument in ('result', 'result++', '(result = input)', '*(s32 *)&result'):
            with self.subTest(argument=argument):
                self.assertFalse(self.generated(BASE.replace('callback(input) >=', f'callback({argument}) >=')))
        for escape in ('s32 *p = &result;', 'void *p = (void *)&result;'):
            self.assertFalse(self.generated(BASE.replace(' result = -542;', f' {escape}\n result = -542;')))

    def test_other_argument_side_effect_is_evaluated_once_in_same_order(self):
        source = self.generated(BASE.replace('callback(input) >=', 'callback(input++) >='))[0][1]
        self.assertEqual(source.count('input++'), 1)
        self.assertLess(source.index('callback(input++)'), source.index('result = -542;'))
        self.assertIn('result = input;', source)

    def test_nested_call_and_other_comparison_side_effect_rejected(self):
        for predicate in ('callback(callback(input)) >= 0', 'callback(input) >= input++',
                          '(callback(input), callback(input)) >= 0', 'callback(input) >= result',
                          'callback(input) >= (result = 0)'):
            with self.subTest(predicate=predicate):
                self.assertFalse(self.generated(BASE.replace('callback(input) >= 0', predicate)))

    def test_default_shadow_and_source_typedef_rejected(self):
        self.assertFalse(self.generated(BASE.replace(' result = input;', ' s32 result; result = input;')))
        self.assertFalse(self.generated(BASE.replace(' s32 result;', ' typedef int local; local result;')))

    def test_fresh_name_collision_avoided(self):
        source = self.generated(BASE.replace(' s32 result;', ' s32 callback_status;\n s32 callback_status_0;\n s32 result;'))[0][1]
        self.assertIn('callback_status_1 = callback(input);', source)

    def test_foreign_preprocessor_and_line_splices_rejected(self):
        for prefix in ('#include "other.h"\n', '#define X 1\n', '#pragma pack(1)\n',
                       '#if 0\n', '#undef callback\n', '\\\n'):
            with self.subTest(prefix=prefix):
                self.assertFalse(self.generated(prefix + BASE))

    def test_flags_may_not_be_activated_by_emission(self):
        source = '\n\n\n/* cflags: -O2 -G0 */\n' + BASE
        self.assertFalse(self.generated(source))

    def test_valid_first_line_flags_preserved(self):
        flags = '/* cflags: -O2 -G0 */'
        source = self.generated(flags + '\n' + BASE)[0][1]
        self.assertEqual(source.splitlines()[0], flags)
        self.assertEqual(len(re.findall(r'/\* cflags:', source)), 1)

    def test_prelude_parser_failures_do_not_escape_generator(self):
        # Parse errors normally fail in rules.parse already; this additionally
        # covers providers whose source parse and standalone prelude parse differ.
        original = module.Variants
        class UnreliablePrelude(original):
            def __init__(self, repository):
                super().__init__(repository)
                self._parse_source = False
            def parse(self, source):
                result = super().parse(source)
                self._parse_source = True
                return result
            def parser(self):
                raise RuntimeError('unreliable parser')
        # Real Variants stores its parser as an instance attribute, so inject
        # the failing factory only after the successful source parse.
        class FailureAfterSource(original):
            def parse(self, source):
                value = super().parse(source)
                def fail():
                    raise ValueError('prelude unavailable')
                self.parser = fail
                return value
        with patch.object(module, 'Variants', FailureAfterSource):
            self.assertFalse(self.generated())


if __name__ == '__main__':
    unittest.main()
