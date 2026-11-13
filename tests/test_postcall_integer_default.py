from pathlib import Path
import unittest,sys
from unittest.mock import patch
import postcall_integer_default as module
from postcall_integer_default import generate

ROOT = (Path(__file__).parent / 'fixtures/project')
BASE = '''#include "common.h"
s32 callback(s32 value);
s32 example(s32 input) {
 s32 result;
 result = -542;
 if (callback(input) >= 0) { result = input; }
 return result;
}
'''

class GuardTests(unittest.TestCase):
 def generated(self, text=BASE): return list(generate(text, ROOT))
 def test_callback_precedes_private_default_and_is_not_duplicated(self):
  source=self.generated()[0][1]
  self.assertEqual(source.count('= callback(input);'),1)
  self.assertLess(source.index('= callback(input);'), source.index('result = -542;'))
  self.assertIn('if (callback_status >= 0)',source)
  self.assertFalse(self.generated(source))
 def test_callback_cannot_observe_prior_default(self):
  self.assertFalse(self.generated(BASE.replace('callback(input) >=','callback(result) >=')))
  self.assertFalse(self.generated(BASE.replace(' result = -542;', ' s32 *p = &result;\n result = -542;')))
 def test_nonlocal_and_static_defaults_are_not_private(self):
  self.assertFalse(self.generated(BASE.replace(' s32 result;', ' static s32 result;')))
  self.assertFalse(self.generated(BASE.replace('s32 example(s32 input)', 's32 result;\ns32 example(s32 input)').replace(' s32 result;','')))
 def test_qualifiers_shadowing_and_macros_are_rejected(self):
  for source in [BASE.replace(' s32 result;', ' volatile s32 result;'),
                 BASE.replace(' s32 result;', ' typedef unsigned int s32;\n s32 result;'),
                 '#define callback other\n'+BASE,
                 BASE.replace(' s32 result;', ' s32 (*callback)(s32);\n s32 result;')]:
   self.assertFalse(self.generated(source))
 def test_unsigned_pointer_wide_and_unknown_call_returns_are_rejected(self):
  for declaration in ['u32 callback(s32 value);','s64 callback(s32 value);',
                      'void *callback(s32 value);','']:
   self.assertFalse(self.generated(BASE.replace('s32 callback(s32 value);',declaration)))
 def test_effectful_or_converted_defaults_are_not_moved(self):
  for value in ['input++','(s32)0x80000000U','-0x80000000','callback(input)','input']:
   self.assertFalse(self.generated(BASE.replace('-542',value)))
 def test_nested_calls_multiple_calls_and_entry_labels_are_rejected(self):
  for source in [BASE.replace('callback(input) >=','callback(callback(input)) >='),
                 BASE.replace('callback(input) >= 0','callback(input) >= callback(input)'),
                 BASE.replace(' if (callback',' entry: if (callback')]:
   self.assertFalse(self.generated(source))
 def test_default_used_only_in_selected_body_stays_in_that_body(self):
  source=self.generated(BASE.replace('result = input;', 'result = result + input;'))[0][1]
  self.assertIn('result = result + input;',source)
 def test_extension_bearing_declarations_are_rejected_before_copy(self):
  original=module.Variants
  for field in ['align','gcc_attributes','asmlabel','funcspec']:
   class ExtensionRules(original):
    def parse(self, text):
     tree,fn=super().parse(text)
     declaration=next(n for *_,n in self._nodes(fn) if isinstance(n,self.a.Decl) and n.name=='result')
     values={'align':[self.a.Alignas(self.a.Constant('int','16'))],
             'gcc_attributes':[self.a.GccAttribute('cleanup',self.a.ExprList([self.a.ID('clean')]))],
             'asmlabel':self.a.Constant('string','"result_register"'),
             'funcspec':['inline']}
     setattr(declaration,field,values[field])
     return tree,fn
   with patch.object(module,'Variants',ExtensionRules):
    self.assertFalse(self.generated(),field)
 def test_asm_register_and_builtin_calls_are_not_transformed(self):
  for source in [BASE.replace(' s32 result;',' register s32 result;'),
                 BASE.replace('callback','__builtin_apply'),
                 BASE.replace(' result = -542;',' __asm__("nop");\n result = -542;')]:
   self.assertFalse(self.generated(source))

if __name__=='__main__':unittest.main()
