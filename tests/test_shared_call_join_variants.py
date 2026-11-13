import unittest
import hashlib,json
from pathlib import Path
from search_rules import Variants
from shared_call_join_variants import children

ROOT=Path(__file__).parent / 'fixtures' / 'project'
BASE='''#include "common.h"
void copy(void *d, const void *s, u32 n);
f32 interpolate(f32 a, f32 b, f32 t);
void sample(void *dst, void *left, void *right, f32 amount) {
 void *selected;
 if (left == right || amount <= 0.0f) { selected = left; goto join; }
 if (amount >= 1.0f) { selected = right; join: copy(dst, selected, 32U); return; }
 *(f32 *)dst = interpolate(*(f32 *)left, *(f32 *)right, amount);
}
'''

class SharedCallJoinTests(unittest.TestCase):
 def setUp(self):self.rules=Variants(ROOT)
 def outputs(self,s):return children(s,self.rules)
 def test_positive_restructures_without_float_complement_or_duplicate_runtime_call(self):
  out=self.outputs(BASE);self.assertEqual(len(out),1);text=out[0][0]
  self.assertIn('amount <= 0.0f',text);self.assertIn('amount >= 1.0f',text)
  self.assertNotIn('goto ',text);self.assertNotIn('selected',text)
  self.assertEqual(text.count('copy(dst,'),2)
  self.assertEqual(text.count('interpolate(*'),1)
  self.assertIn('else',text)
 def test_alpha_renaming_not_address_or_function_special_case(self):
  text=BASE.replace('sample','another').replace('copy','transfer').replace('selected','relay').replace('32U','41U')
  out=self.outputs(text);self.assertEqual(len(out),1);self.assertIn('transfer(dst, left, 41U)',out[0][0])
 def test_reject_escaped_relay_or_additional_use(self):
  self.assertEqual(self.outputs(BASE.replace('copy(dst, selected, 32U)','copy(&selected, selected, 32U)')),[])
  self.assertEqual(self.outputs(BASE.replace('*(f32 *)dst =','selected = dst; *(f32 *)dst =')),[])
 def test_reject_side_effecting_argument(self):
  self.assertEqual(self.outputs(BASE.replace('copy(dst, selected, 32U)','copy(next(), selected, 32U)')),[])
 def test_reject_nonparameter_selection_or_wrong_type(self):
  self.assertEqual(self.outputs(BASE.replace('selected = left','selected = get()')),[])
  self.assertEqual(self.outputs(BASE.replace('void *left','s32 *left')),[])
 def test_reject_volatile_static_and_macro_types(self):
  for text in [BASE.replace('void *selected','void * volatile selected'),BASE.replace('void *selected','static void *selected'),'#define selected changed\n'+BASE]:
   self.assertEqual(self.outputs(text),[])
 def test_reject_additional_control_flow_entry(self):
  self.assertEqual(self.outputs(BASE.replace('*(f32 *)dst =','goto join; *(f32 *)dst =')),[])
  self.assertEqual(self.outputs(BASE.replace('*(f32 *)dst =','other: *(f32 *)dst =')),[])
 def test_reject_nonvoid_or_changed_return(self):
  self.assertEqual(self.outputs(BASE.replace('void sample','s32 sample').replace('return;','return 1;')),[])
  self.assertEqual(self.outputs(BASE.replace('return;','return copy(dst,left,0);')),[])
 def test_reject_shadowed_relay_pointer_or_callee(self):
  self.assertEqual(self.outputs(BASE.replace('void *selected;','void *selected; void *copy;')),[])
 def test_reject_reordered_or_extra_join_side_effect(self):
  self.assertEqual(self.outputs(BASE.replace('join: copy','other(); join: copy')),[])
 def test_reject_tail_declarations_and_return(self):
  self.assertEqual(self.outputs(BASE.replace('*(f32 *)dst =','return; *(f32 *)dst =')),[])
 def test_source_location_macros_cannot_change_initial_values(self):
  for macro in ('__LINE__','__FILE__','__COUNTER__'):
   self.assertEqual(self.outputs(BASE.replace('32U',macro)),[])
 @unittest.skip("Original game/compiler review evidence stays in the originating local repository.")
 def test_real_claimed_positive_has_same_conditions_signature_and_tail_call_order(self):
  source=(ROOT/'build/review_auto15/func_L01_004515A8/baseline.c').read_text()
  out=self.outputs(source);self.assertEqual(len(out),1)
  before,fn=self.rules.parse(source);after,nfn=self.rules.parse(out[0][0])
  self.assertEqual(self.rules.generator.visit(fn.decl),self.rules.generator.visit(nfn.decl))
  self.assertEqual(out[0][0].count('func_00475FB0('),6)
  self.assertEqual(out[0][0].count('func_00475F88('),5)
 @unittest.skip("Original game/compiler review evidence stays in the originating local repository.")
 def test_real_generated_source_object_and_all_placement_targets(self):
  from auto_engine import AutoEngine
  scope=ROOT/'build/review_auto15';proof=json.loads((scope/'algorithm_a/compiler-evidence.json').read_text())
  row=proof['generated'];source=(scope/'func_L01_004515A8/baseline.c').read_text(encoding='utf-8')
  generated=self.outputs(source)[0][0];p=Path(row['snapshot'])
  self.assertEqual(generated,p.read_text(encoding='utf-8'))
  self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),row['sourceHash'])
  self.assertEqual(hashlib.sha256(p.with_suffix('.o').read_bytes()).hexdigest(),row['objectHash'])
  result=json.loads(p.with_name('result.json').read_text(encoding='utf-8'))
  strict=AutoEngine(ROOT).strict(row['name'],result)
  self.assertTrue(strict['passed']);self.assertEqual(len(strict['placements']),13)
  self.assertEqual(sum(len(p['targets']['checked']) for p in strict['placements']),130)
  # The rule preserves prototypes. Semantic ABI approval belongs to the final
  # reviewed source, independently of a technically matching generated draft.
  abi=json.loads((scope/'abi-reference/00475F88-placement-evidence.json').read_text())
  self.assertTrue(abi['semanticReviewPassed']);self.assertEqual(len(abi['placements']),15)

if __name__=='__main__':unittest.main()
