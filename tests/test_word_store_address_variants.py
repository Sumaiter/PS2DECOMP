from pathlib import Path
import sys,unittest,struct
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from word_store_address_variants import generate,native_store
ROOT=Path(__file__).parent / 'fixtures' / 'project'
SOURCE='''#include "common.h"
void f(void *arg, s32 start, s32 value) {
 s32 *base; s32 idx; idx=start;
 base=(s32 *)((s8 *)arg+0x378);
 if (idx>=0) base[idx]=value;
}
'''
BODY=struct.pack('<6I',0x24920378,0x04A00003,0,0x00051880,0x02431821,0xac660000)
class WordStoreTests(unittest.TestCase):
 def gen(self,s=SOURCE,b=BODY,bits=32):return list(generate(s,ROOT,[b],bits))
 def test_positive(self):
  out=self.gen();self.assertEqual(len(out),1);self.assertIn('(u32) base',out[0][1]);self.assertIn('(u32) idx',out[0][1])
 def test_pointer_width_unknown_or_64(self):
  self.assertFalse(self.gen(bits=None));self.assertFalse(self.gen(bits=64))
 def test_unguarded_negative_signed_index(self):self.assertFalse(self.gen(SOURCE.replace('if (idx>=0)','if (idx!=-1)')))
 def test_side_effect_base(self):self.assertFalse(self.gen(SOURCE.replace('base[idx]','get()[idx]')))
 def test_escaped_index(self):self.assertFalse(self.gen(SOURCE.replace('if (idx>=0)','escape(&idx); if (idx>=0)')))
 def test_mutated_index(self):self.assertFalse(self.gen(SOURCE.replace('if (idx>=0) base[idx]=value;','if (idx>=0) {idx--; base[idx]=value;}')))
 def test_qualified_or_shadowed(self):
  self.assertFalse(self.gen(SOURCE.replace('s32 idx','volatile s32 idx')))
  self.assertFalse(self.gen(SOURCE.replace('if (idx>=0) base[idx]=value;','if (idx>=0) {s32 idx; base[idx]=value;}')))
 def test_unrelated_shift_and_addu(self):
  self.assertFalse(self.gen(b=BODY.replace(struct.pack('<I',0x02431821),struct.pack('<I',0x02441821))))
 def test_wrong_store_register(self):
  self.assertFalse(self.gen(b=BODY.replace(struct.pack('<I',0xac660000),struct.pack('<I',0xac860000))))
 def test_wrong_base_definition_or_offset(self):self.assertFalse(self.gen(b=BODY.replace(struct.pack('<I',0x24920378),struct.pack('<I',0x24b20378))))
 def test_all_placements_required(self):self.assertFalse(list(generate(SOURCE,ROOT,[BODY,BODY[:-4]],32)))
 def test_shift_does_not_overwrite_base(self):
  bad=struct.pack('<6I',0x24920378,0x04A00003,0,0x00059080,0x02529021,0xae460000)
  self.assertFalse(self.gen(b=bad))
 def test_zero_register_is_not_index(self):
  self.assertFalse(self.gen(b=BODY.replace(struct.pack('<I',0x00051880),struct.pack('<I',0x00001880))))
 def test_guard_decrements_index_after_lower_bound(self):self.assertFalse(self.gen(SOURCE.replace('idx>=0','idx>=0 && (--idx,1)')))
 def test_guard_assigns_negative_index_after_lower_bound(self):self.assertFalse(self.gen(SOURCE.replace('idx>=0','idx>=0 && (idx=-1)')))
 def test_guard_mutates_pointer_base(self):self.assertFalse(self.gen(SOURCE.replace('idx>=0','idx>=0 && (base++,1)')))
 def test_parameter_escaped_or_mutated(self):
  self.assertFalse(self.gen(SOURCE.replace('idx=start;','idx=start; escape(&arg);')))
  self.assertFalse(self.gen(SOURCE.replace('idx=start;','idx=start; arg=other;')))
 def test_signature_preserved(self):
  from search_rules import Variants
  r=Variants(ROOT);x=self.gen()[0][1]
  self.assertEqual(r.generator.visit(r.parse(SOURCE)[1].decl),r.generator.visit(r.parse(x)[1].decl))
if __name__=='__main__':unittest.main()
