"""Shared rules use actual headers; native evidence belongs to its adapter."""
from pathlib import Path
from types import SimpleNamespace
import copy
import tempfile
import unittest
from unittest.mock import patch

import auto_jobs
from search_rules import Variants
import scalar_definition_retarget
import postcall_integer_default
from RAC4_PAL.engine import AutoEngine as PalEngine

FIXTURE = Path(__file__).parent / 'fixtures/project'


class HeaderAndAdapterTests(unittest.TestCase):
    def test_alias_names_are_not_game_types(self):
        # A differently named header, including a double-precision alias,
        # must be resolved as declared rather than guessed from its spelling.
        for leaf in ('float', 'double', 'int', 'unsigned int'):
            with self.subTest(leaf=leaf), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); (root/'include').mkdir()
                (root/'include/common.h').write_text('typedef '+leaf+' Real;\n', encoding='utf-8')
                rules = Variants(root)
                source = '#include "common.h"\nReal f(Real x){Real a;Real b;a=x;b=a*a;return b;}'
                children = scalar_definition_retarget.children(source, rules)
                self.assertEqual(len(children), 1)
                self.assertIn('b = x;', children[0][1])
                self.assertIn('b = b * b;', children[0][1])
                self.assertNotIn('f32', children[0][1])

    def test_different_actual_types_do_not_merge_even_if_aliases_sound_alike(self):
        rules = Variants(FIXTURE)
        rules.prelude = 'typedef float word32; typedef double real32;'
        rules.header_text = rules.prelude
        src = '#include "common.h"\nreal32 f(word32 x){word32 a;real32 b;a=x;b=a*a;return b;}'
        self.assertFalse(scalar_definition_retarget.children(src, rules))

    def test_integer_default_respects_declared_int_and_portable_literal_range(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'include').mkdir()
            header=root/'include/common.h'
            for leaf, expected in [('int', True), ('short', False), ('long', False), ('unsigned int', False)]:
                header.write_text('typedef '+leaf+' Status;\n', encoding='utf-8')
                src='#include "common.h"\nStatus call(void);Status f(void){Status x;x=-542;if(call()>=0)x=0;return x;}'
                self.assertEqual(bool(list(postcall_integer_default.generate(src, root))), expected)
            header.write_text('typedef int Status;\n', encoding='utf-8')
            self.assertFalse(list(postcall_integer_default.generate(src.replace('-542', '32768'), root)))

    def test_generic_engine_does_not_read_native_images_for_a_shared_candidate(self):
        rules=Variants(FIXTURE)
        class Generic:
            root=FIXTURE
            def all_places(self, *_): raise AssertionError('Unexpected native read')
            def assert_reference(self, *_): raise AssertionError('Unexpected native read')
        src='#include "common.h"\nf32 f(f32 x){f32 a;f32 b;a=x;b=a*a;return b;}'
        children=list(auto_jobs.reviewed_local_children(src, rules, Generic(), 'f', None, b''))
        self.assertEqual([label for label,_ in children], ['private-scalar-definition-retarget'])

    def test_optional_adapter_hook_does_not_change_shared_candidates(self):
        rules=Variants(FIXTURE)
        src='#include "common.h"\nf32 f(f32 x){f32 a;f32 b;a=x;b=a*a;return b;}'
        received=[]
        engine=SimpleNamespace(root=FIXTURE, additional_search_children=lambda *args: received.append(args) or [('adapter-rule',src)])
        children=list(auto_jobs.reviewed_local_children(src,rules,engine,'f',None,b''))
        self.assertEqual(received,[('f',src,rules)])
        self.assertEqual([label for label,_ in children], ['private-scalar-definition-retarget','adapter-rule'])

    def test_pal_engine_owns_the_native_hook(self):
        engine=object.__new__(PalEngine)
        with patch('RAC4_PAL.native_local_after_call_wrapper.generate_for_engine',return_value=[]) as method:
            self.assertEqual(engine.additional_search_children('f','source','rules'),[])
        method.assert_called_once_with(engine,'f','source',rules='rules')

    def test_terminal_cast_load_rejects_attributes_hidden_on_typedef_nodes(self):
        rules=Variants(FIXTURE)
        for definition in ('typedef float Scalar __attribute__((mode(DF)));',
                           'typedef volatile float Base; typedef Base Scalar;',
                           'typedef float Scalar; typedef float Scalar;'):
            with self.subTest(definition=definition):
                alternate=copy.copy(rules);alternate.prelude=definition
                alternate.header_text=definition
                src='#include "common.h"\nScalar f(int n,void *p){if(n)return (Scalar)0;return *(Scalar *)p;}'
                self.assertFalse([c for label,c in alternate.generate(src) if label=='terminale-rueckgaben-verbinden'])

    def test_terminal_join_cannot_activate_flags_or_relocate_line_macros(self):
        rules=Variants(FIXTURE)
        src='#include "common.h"\nint f(int n,void *p){if(n)return 0;return *(int *)p;}'
        for bad in ('\n/* cflags: -O2 */\n'+src,src+'\n/* cflags: -O2 */',
                    '  /* cflags: -O2 */\n'+src,
                    '#inc\\\nlude "common.h"\n'+src):
            with self.subTest(source=bad),self.assertRaises(ValueError):list(rules.generate(bad))
        for address in ('__LINE__','__COUNTER__','__builtin_frame_address()'):
            bad=src.replace('*(int *)p','*(int *)'+address)
            self.assertFalse([c for label,c in rules.generate(bad) if label=='terminale-rueckgaben-verbinden'])

    def test_header_macros_and_incomplete_preprocessing_never_enter_new_rules(self):
        source='#include "common.h"\nfloat f(float x){float t;float u;t=x;u=t*t;return u;}'
        rules=Variants(FIXTURE)
        for extra in ('#define float volatile float', '#define callback other', '#include "hidden.h"',
                      '#if ACTIVE\ntypedef float hidden;\n#endif', 'extern int hidden;'):
            with self.subTest(extra=extra):
                alternate=copy.copy(rules);alternate.header_text=rules.header_text+'\n'+extra
                self.assertFalse(alternate.plain_header())
                self.assertFalse(scalar_definition_retarget.children(source,alternate))
                self.assertFalse([c for label,c in alternate.generate('#include "common.h"\nint f(int n,void *p){if(n)return 0;return *(int*)p;}')
                                  if label=='terminale-rueckgaben-verbinden'])


if __name__ == '__main__': unittest.main()
