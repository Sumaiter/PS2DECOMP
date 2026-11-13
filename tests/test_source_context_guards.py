"""AST regeneration must not change source-dependent macro values anywhere."""
from pathlib import Path
import unittest

import postcall_integer_default
from search_rules import Variants

ROOT = Path(__file__).parent / 'fixtures/project'
CALLBACK = '''#include "common.h"
int callback(int value);
int example(int input) {
 int result;
 result = -1;
 if (callback(input) >= 0) result = 7;
 return result;
}
'''
TERMINAL = '''#include "common.h"
int example(int input, void *p) {
 if (input) return 0;
 return *(int *)p;
}
'''


class SourceContextGuards(unittest.TestCase):
    def setUp(self):
        self.rules = Variants(ROOT)

    def callback_children(self, source):
        return list(postcall_integer_default.generate(source, ROOT))

    def terminal_children(self, source):
        return [text for label, text in self.rules.generate(source)
                if label == 'terminale-rueckgaben-verbinden']

    def test_callback_argument_with_source_builtin_stays_manual(self):
        for expression in ('__LINE__', '__COUNTER__', '__INCLUDE_LEVEL__',
                           '__FILE__[0]', '__BASE_FILE__[0]', '__FILE_NAME__[0]',
                           '__DATE__[0]', '__TIME__[0]', '__TIMESTAMP__[0]'):
            with self.subTest(expression=expression):
                source = CALLBACK.replace('callback(input) >=', 'callback(' + expression + ') >=')
                self.assertEqual(self.callback_children(source), [])

    def test_source_builtin_in_global_initializer_blocks_both_rules(self):
        for expression in ('__LINE__', '__COUNTER__', '__FILE__[0]', '__INCLUDE_LEVEL__'):
            for template, children in ((CALLBACK, self.callback_children),
                                       (TERMINAL, self.terminal_children)):
                with self.subTest(expression=expression, rule=children.__name__):
                    source = template.replace('#include "common.h"\n',
                        '#include "common.h"\nint marker = ' + expression + ';\n')
                    self.assertEqual(children(source), [])

    def test_source_builtin_in_global_array_bound_blocks_both_rules(self):
        for template, children in ((CALLBACK, self.callback_children),
                                   (TERMINAL, self.terminal_children)):
            source = template.replace('#include "common.h"\n',
                '#include "common.h"\nint table[__LINE__];\n')
            with self.subTest(rule=children.__name__):
                self.assertEqual(children(source), [])

    def test_macro_spelling_in_comments_and_strings_is_not_an_identifier(self):
        for template, children in ((CALLBACK, self.callback_children),
                                   (TERMINAL, self.terminal_children)):
            source = template.replace('#include "common.h"\n',
                '#include "common.h"\n/* __LINE__ __COUNTER__ */\n'
                'char *description = "__LINE__ __FILE__";\n')
            with self.subTest(rule=children.__name__):
                self.assertEqual(len(children(source)), 1)

    def test_plain_ordinary_global_identifier_keeps_both_rules_available(self):
        callback = CALLBACK.replace('#include "common.h"\n',
            '#include "common.h"\nint marker = 42;\n').replace('callback(input) >=', 'callback(marker) >=')
        terminal = TERMINAL.replace('#include "common.h"\n',
            '#include "common.h"\nint marker = 42;\n').replace('return 0;', 'return marker;')
        self.assertEqual(len(self.callback_children(callback)), 1)
        self.assertEqual(len(self.terminal_children(terminal)), 1)


if __name__ == '__main__':
    unittest.main()
