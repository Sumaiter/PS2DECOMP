import copy, sys, unittest
from pathlib import Path
from search_rules import Variants
import scalar_definition_retarget as rule

class GuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = Variants((Path(__file__).parent / 'fixtures/project'))

    def generated(self, body, decl='f32 t; f32 u;', ret='u', args='f32 x'):
        return rule.generate(self.rules, '#include "common.h"\nf32 test('+args+') { '+decl+body+'return '+ret+'; }')

    def test_same_f32_assignment_rounding_and_multiply_tree_preserved(self):
        out=self.generated('t=x; u=t*t;')
        self.assertIsNotNone(out)
        _,fn=self.rules.parse(out)
        assignments=[n for *_,n in self.rules._nodes(fn.body) if isinstance(n,self.rules.a.Assignment)]
        self.assertEqual([self.rules.generator.visit(n) for n in assignments],['u = x','u = u * u'])

    def test_crosses_only_independent_plain_scalar_assignments(self):
        self.assertIsNotNone(self.generated('t=x; count=1; u=t*t;',decl='f32 t; f32 u; s32 count;'))
        self.assertIsNotNone(self.generated('t=x; p=0; u=t*t;',decl='f32 t; f32 u; f32 *p;'))

    def test_rejects_target_read_before_consumer(self):
        self.assertIsNone(self.generated('u=x; t=x; u=t*t;'))
        self.assertIsNone(self.generated('t=x; u=t+u;'))
        self.assertIsNone(self.generated('t=u; u=t*t;'))

    def test_rejects_live_temporary_and_address_escape(self):
        self.assertIsNone(self.generated('t=x; u=t*t; x=t;',ret='u+x'))
        self.assertIsNone(self.generated('t=x; u=t*t; p=&t;',decl='f32 t; f32 u; f32 *p;'))
        self.assertIsNone(self.generated('t=x; u=t*t; p=&u;',decl='f32 t; f32 u; f32 *p;'))

    def test_rejects_type_conversion_and_qualifiers(self):
        self.assertIsNone(self.generated('t=x; u=t*t;',decl='f32 t; f64 u;'))
        self.assertIsNone(self.generated('t=x; u=t*t;',decl='volatile f32 t; f32 u;'))
        self.assertIsNone(self.generated('t=x; u=t*t;',decl='register f32 t; f32 u;'))

    def test_rejects_control_flow_calls_memory_consumer_and_shadow(self):
        self.assertIsNone(self.generated('t=x; if(x) x=1; u=t*t;'))
        self.assertIsNone(self.generated('t=x; u=t*callee();'))
        self.assertIsNone(self.generated('t=x; u=t*(*p);',decl='f32 t; f32 u; f32 *p;'))
        self.assertIsNone(self.generated('t=x; u=t*t; { f32 t; t=0; }'))

    def test_rejects_initializers_and_self_dependent_temporary(self):
        self.assertIsNone(self.generated('t=x; u=t*t;',decl='f32 t=0; f32 u;'))
        self.assertIsNone(self.generated('t=t+x; u=t*t;'))

    def test_rejects_foreign_preprocessor_context(self):
        source='#include "common.h"\nf32 test(f32 x) { f32 t; f32 u; t=x; u=t*t; return u; }'
        for directive in ('#include "foreign.h"','#define f32 float','#if 1','#pragma once','#undef f32'):
            self.assertIsNone(rule.generate(self.rules,directive+'\n'+source))
        self.assertIsNone(rule.generate(self.rules,'#inc\\\nlude "common.h"\n'+source))

    def test_resolves_real_prelude_hidden_qualifiers_unknowns_and_cycles(self):
        source='#include "common.h"\nf32 test(f32 x) { f32 t; f32 u; t=x; u=t*t; return u; }'
        for prelude in ('typedef volatile float f32;',
                        'typedef const float base; typedef base f32;',
                        'typedef float *f32;',
                        'typedef missing f32;',
                        'typedef float f32; typedef f32 f32;',
                        'typedef f32 f32;'):
            alternate=copy.copy(self.rules);alternate.prelude=prelude;alternate.header_text=prelude
            self.assertIsNone(rule.generate(alternate,source),prelude)
        alternate=copy.copy(self.rules);alternate.prelude='typedef float base; typedef base f32;'
        alternate.header_text=alternate.prelude
        self.assertIsNotNone(rule.generate(alternate,source))

    def test_preserves_canonical_first_line_flags_only(self):
        source='#include "common.h"\nf32 test(f32 x) { f32 t; f32 u; t=x; u=t*t; return u; }'
        flags='/* cflags: -O2 -G0 */'
        out=rule.generate(self.rules,flags+'\n'+source)
        self.assertIsNotNone(out)
        self.assertEqual(out.splitlines()[0],flags)
        self.assertIsNone(rule.generate(self.rules,source+'\n'+flags))

if __name__=='__main__': unittest.main()
