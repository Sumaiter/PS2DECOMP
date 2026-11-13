from pathlib import Path
import sys,unittest,json
sys.path.append(str(Path(__file__).resolve().parents[1]))
from search_rules import Variants
from terminal_scalar_parameter import generate
BASE="f32 f(f32 x){f32 a;f32 b;if(x<0.0f){a=x+1.0f;a=a*2.0f;return a;}else{b=x-1.0f;return b;}}"
class Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.rules=Variants(Path(__file__).parent / 'fixtures' / 'project')
 def test_guards(self):
  cases={
   'positive':(BASE,True),
   'address_escape':(BASE.replace('a=a*2.0f','a=*(f32 *)&x'),False),
   'old_parameter_read_later':(BASE.replace('a=a*2.0f','a=a+x'),False),
   'uninitialized_local_read':(BASE.replace('a=x+1.0f','a=a+x'),False),
   'function_call':(BASE.replace('a=a*2.0f','a=callback(a)'),False),
   'volatile_parameter':(BASE.replace('f32 x','volatile f32 x'),False),
   'volatile_local':(BASE.replace('f32 a','volatile f32 a'),False),
   'typedef_override':('typedef double f32;'+BASE,False),
   'source_builtin':(BASE.replace('a=a*2.0f','a=a+__LINE__'),False),
   'local_initializer':(BASE.replace('f32 a;','f32 a=0.0f;'),False),
   'parameter_shadow':(BASE.replace('f32 a;','f32 x;'),False),
   'wide_local':(BASE.replace('f32 a;','f64 a;'),False),
   'integer_parameter':(BASE.replace('f32 x','s32 x'),False),
   'nonterminal_if':(BASE[:-1]+'return x;}',False),
   'label_entry':(BASE.replace('a=x+1.0f','start:a=x+1.0f'),False),
   'mixed_branch_locals':(BASE.replace('b=x-1.0f','b=a+x'),False),
   'macro':('#define f32 double\n'+BASE,False),
   'flags':('/* cflags: -mno-split-addresses */\n'+BASE,True),
   'parameter_has_function_name':(BASE.replace('f32 f(', 'f32 x('),False),
   'local_has_function_name':(BASE.replace('f32 f(', 'f32 a('),False),
   'recursive_call':(BASE.replace('a=a*2.0f','a=f(a)'),False),
   'parameter_pointer':(BASE.replace('f32 x','f32 *x'),False),
   'pointer_local':(BASE.replace('f32 a','f32 *a'),False),
   'wide_return':(BASE.replace('f32 f','f64 f'),False),
   'register_parameter':(BASE.replace('f32 x','register f32 x'),False),
   'const_parameter':(BASE.replace('f32 x','const f32 x'),False),
   'static_local':(BASE.replace('f32 a','static f32 a'),False),
   'address_of_local':(BASE.replace('a=a*2.0f','a=(f32)&a'),False),
   'indirect_read':(BASE.replace('a=a*2.0f','a=*(f32*)4'),False),
   'post_increment':(BASE.replace('a=a*2.0f','a=a++'),False),
   'compound_assignment':(BASE.replace('a=a*2.0f','a*=2.0f'),False),
   'comma_operator':(BASE.replace('a=a*2.0f','a=(a,a*2.0f)'),False),
   'nested_block_shadow':(BASE.replace('a=a*2.0f','{f32 a;a=2.0f;}'),False),
   'goto':(BASE.replace('a=a*2.0f','goto target'),False),
   'extra_parameter':(BASE.replace('f32 x','f32 x,f32 y'),False),
   'source_file_macro':(BASE.replace('a=a*2.0f','a=__FILE__'),False),
   'source_func_macro':(BASE.replace('a=a*2.0f','a=__func__'),False),
   'source_counter_macro':(BASE.replace('a=a*2.0f','a=__COUNTER__'),False),
   'line_directive':('#line 900\n'+BASE,False),
   'source_builtin_call':(BASE.replace('a=a*2.0f','a=__builtin_fabsf(a)'),False),
  }
  results=[]
  for label,(source,want) in cases.items():
   with self.subTest(case=label):
    after=generate(self.rules,source);self.assertEqual(after is not None,want)
    results.append({'case':label,'accepted':after is not None,'expected':want})
    if label=='flags':self.assertTrue(after.startswith('/* cflags: -mno-split-addresses */'))
  self.assertEqual(len(results),40)
 def test_operations_unchanged(self):
  actual=generate(self.rules,BASE)
  expected='f32 f(f32 x){if(x<0.0f){x=x+1.0f;x=x*2.0f;return x;}else{x=x-1.0f;return x;}}'
  self.assertEqual(self.rules.generator.visit(self.rules.parse(actual)[0]),self.rules.generator.visit(self.rules.parse(expected)[0]))
 def test_cached_ast_immutable(self):
  rules=self.rules;parsed=rules.parse(BASE);before=rules.generator.visit(parsed[0])
  class Cached:
   a=rules.a
   parser=rules.parser
   prelude=rules.prelude
   _nodes=rules._nodes
   _emit=rules._emit
   def parse(self,text):return parsed if text==BASE else rules.parse(text)
  self.assertIsNotNone(generate(Cached(),BASE));self.assertEqual(before,rules.generator.visit(parsed[0]))
 def test_hidden_qualified_wide_and_recursive_aliases(self):
  rules=self.rules
  for declaration in ('typedef volatile float f32;', 'typedef const float f32;',
                      'typedef double f32;', 'typedef f32 f32;',
                      'typedef cycle f32; typedef f32 cycle;'):
   class Alternate:
    a=rules.a
    parser=rules.parser
    prelude=declaration
    _nodes=rules._nodes
    _emit=rules._emit
    def parse(self,text):return rules.parse(text)
   with self.subTest(declaration=declaration):self.assertIsNone(generate(Alternate(),BASE))
  source=BASE.replace('a=a*2.0f','a=(f32)(s32)a')
  class WrongInteger:
   a=rules.a
   parser=rules.parser
   prelude='typedef float f32; typedef unsigned int s32;'
   _nodes=rules._nodes
   _emit=rules._emit
   def parse(self,text):return rules.parse(text)
  self.assertIsNone(generate(WrongInteger(),source))
 def test_nan_conditions_signed_zero_and_arm_order_are_unchanged(self):
  for condition in ('x < 0.0f','x != x','x == x','!(x >= -0.0f)'):
   original='f32 f(f32 x){f32 a;f32 b;if('+condition+'){a=-x;return a;}else{b=x+-0.0f;return b;}}'
   expected='f32 f(f32 x){if('+condition+'){x=-x;return x;}else{x=x+-0.0f;return x;}}'
   actual=generate(self.rules,original)
   self.assertIsNotNone(actual)
   self.assertEqual(self.rules.generator.visit(self.rules.parse(actual)[0]),self.rules.generator.visit(self.rules.parse(expected)[0]))
 @unittest.skip("Original game/compiler review evidence stays in the originating local repository.")
 def test_real_compiler_pair_remains_exactly_bound(self):
  folder=Path(__file__).parent / 'fixtures' / 'project'
  before=(folder/'before.c').read_text(encoding='utf-8')
  after=(folder/'after.c').read_text(encoding='utf-8')
  actual=generate(self.rules,before)
  self.assertIsNotNone(actual)
  self.assertEqual(self.rules.generator.visit(self.rules.parse(actual)[0]),self.rules.generator.visit(self.rules.parse(after)[0]))
  self.assertEqual(self.rules.parse(actual)[1].decl.type.args.params[0].type.type.names,['f32'])
if __name__=='__main__':unittest.main(verbosity=2)
