"""Scalar-copy guards, overlap semantics and retained proof; no compiler calls."""
from pathlib import Path
import copy
import hashlib
import json
import unittest

ROOT = Path(__file__).parent / 'fixtures' / 'project'
SCOPE = ROOT / 'build/review_auto17'
from search_rules import Variants
from scalar_copy_temp import children, generate, LABEL

COPY = '*(s32 *)((s8 *)d+4)=*(s32 *)s; *(s32 *)((s8 *)d+8)=*(s32 *)((s8 *)s+4);'
BASE = 'void temporary(void *d,void *s) { BODY }'
CASES = {
 'matching_s32_run': (COPY, True),
 'one_copy': ('*(s32 *)d=*(s32 *)s;', False),
 'width_mismatch': ('*(s32 *)d=*(s16 *)s; *(s32 *)((s8 *)d+4)=*(s16 *)((s8 *)s+2);', False),
 'signedness_mismatch': ('*(u32 *)d=*(s32 *)s; *(u32 *)((s8 *)d+4)=*(s32 *)((s8 *)s+4);', False),
 'pointer_increment': ('*(s32 *)d++=*(s32 *)s; *(s32 *)d++=*(s32 *)s;', False),
 'call_address': ('*(s32 *)d=*(s32 *)get(); *(s32 *)((s8 *)d+4)=*(s32 *)get();', False),
 'intervening_call': ('*(s32 *)d=*(s32 *)s; callback(); *(s32 *)((s8 *)d+4)=*(s32 *)((s8 *)s+4);', False),
 'separate_control_paths': ('if(d) { *(s32 *)d=*(s32 *)s; } else { *(s32 *)d=*(s32 *)s; }', False),
 'volatile_pointer': ('*(volatile s32 *)d=*(s32 *)s; *(volatile s32 *)((s8 *)d+4)=*(s32 *)((s8 *)s+4);', False),
 'float_copies': ('*(f32 *)d=*(f32 *)s; *(f32 *)((s8 *)d+4)=*(f32 *)((s8 *)s+4);', False),
 'converted_copy_value': ('*(s32 *)d=(f32)*(s32 *)s; *(s32 *)((s8 *)d+4)=(f32)*(s32 *)((s8 *)s+4);', False),
 'native_initialization_arm': ('*(s32 *)d=4; *(s32 *)((s8 *)d+4)=8;', False),
 'sixteen_byte_copies': ('*(s128 *)d=*(s128 *)s; *(s128 *)((s8 *)d+16)=*(s128 *)((s8 *)s+16);', False),
 'nested_volatile_address': ('*(s32 *)*(void *volatile *)d=*(s32 *)s; *(s32 *)((s8 *)*(void *volatile *)d+4)=*(s32 *)s;', False),
 'nested_nonvolatile_address_read': ('*(s32 *)*(void **)d=*(s32 *)s; *(s32 *)((s8 *)*(void **)d+4)=*(s32 *)s;', False),
 'pointer_local_alias': ('void *p=d; ' + COPY, False),
 'pointer_parameter_assignment': ('d=s; ' + COPY, False),
 'pointer_parameter_address_escape': ('void **p=&d; ' + COPY, False),
 'typed_pointer_scaled_offset': ('*(s32 *)((s32 *)d+1)=*(s32 *)s; *(s32 *)((s32 *)d+2)=*(s32 *)s;', False),
 'unknown_offset': ('s32 i=4; *(s32 *)((s8 *)d+i)=*(s32 *)s; *(s32 *)((s8 *)d+i+4)=*(s32 *)s;', False),
 'intervening_label': ('*(s32 *)d=*(s32 *)s; middle: *(s32 *)((s8 *)d+4)=*(s32 *)s;', False),
 'intervening_control': ('*(s32 *)d=*(s32 *)s; if(d)return; *(s32 *)((s8 *)d+4)=*(s32 *)s;', False),
 'null_guard': ('if(s!=NULL) {' + COPY + '}', True),
 'local_shadow': ('{' + COPY + '} {void *d;}', False),
 'source_builtin': (COPY + 's32 x=__LINE__;', False),
 'unbound_identifier': (COPY + 's32 x=undeclared;', False),
 'enum_collision': ('enum {rac4_copy_word=7}; ' + COPY, True),
 'decl_collision': ('s32 rac4_copy_word; ' + COPY, True),
}
EXTRA = {
 'volatile_parameter': ('void f(void *volatile d,void *s){'+COPY+'}', False),
 'const_parameter': ('void f(const void *d,void *s){'+COPY+'}', False),
 'volatile_typedef_override': ('typedef volatile int s32;void f(void *d,void *s){'+COPY+'}', False),
 'local_typedef_override': ('void f(void *d,void *s){typedef volatile int s32;'+COPY+'}', False),
 'typedef_name_collision': ('typedef s32 rac4_copy_word;void f(void *d,void *s){rac4_copy_word x;'+COPY+'}', False),
 'macro_override': ('#define s32 float\nvoid f(void *d,void *s){'+COPY+'}', False),
 'source_file_macro': ('s32 f(void *d,void *s){'+COPY+'return __FILE__[0];}', False),
 'global_address': ('extern void *d;void f(void *s){'+COPY+'}', False),
 'global_pointer_escape': ('extern void *g;void f(void *d,void *s){g=d;'+COPY+'}', False),
 'compound_pointer_escape': ('extern void *g;void f(void *d,void *s){g=(s8 *)d+*(s32 *)s;'+COPY+'}', False),
 'pointer_from_integer': ('void f(u32 d,void *s){'+COPY+'}', False),
 'asm_construct': ('void f(void *d,void *s){asm("nop");'+COPY+'}', False),
 'unrelated_call': ('void callback(void);void f(void *d,void *s){callback();'+COPY+'}', False),
 'typed_parameter_positive': ('void f(s32 *d,u32 *s){'+COPY+'}', True),
 'flags_preserved': ('/* cflags: -mno-split-addresses */\nvoid f(void *d,void *s){'+COPY+'}', True),
}

class ScalarCopyGuardTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.rules = Variants(ROOT)

 def test_guards(self):
  self.results = []
  for label, (body, expected) in CASES.items():
   with self.subTest(case=label):
    output = generate(self.rules, BASE.replace('BODY', body))
    self.assertEqual(output is not None, expected)
    self.results.append({'case':label,'accepted':output is not None,'expected':expected})
    if expected and 'collision' in label:
     self.assertIn('rac4_copy_word_1', output)
  for label, (source, expected) in EXTRA.items():
   with self.subTest(case=label):
    output = generate(self.rules, source)
    self.assertEqual(output is not None, expected)
    self.results.append({'case':label,'accepted':output is not None,'expected':expected})
    if label == 'flags_preserved':
     self.assertTrue(output.startswith('/* cflags: -mno-split-addresses */'))

  self.assertEqual(len(self.results),43)

 def test_parser_ast_is_not_mutated(self):
  source = BASE.replace('BODY', COPY)
  rules, parsed = self.rules, self.rules.parse(source)
  before = rules.generator.visit(parsed[0])
  class CachedRules:
   a = rules.a
   _nodes = rules._nodes
   _emit = rules._emit
   def parse(self, text):
    return parsed if text == source else rules.parse(text)
  self.assertIsNotNone(generate(CachedRules(), source))
  self.assertEqual(rules.generator.visit(parsed[0]), before)

 def test_sequential_overlap(self):
  for source in range(4):
   for destination in range(4):
    initial = list(range(16)); old = initial[:]; new = initial[:]; bulk = initial[:]
    for i in range(6):
     old[destination+i] = old[source+i]
    for i in range(6):
     temporary = new[source+i]
     new[destination+i] = temporary
    bulk[destination:destination+6] = bulk[source:source+6]
    self.assertEqual(old, new)
    if source == 0 and destination == 1:
     self.assertNotEqual(old, bulk)

 @unittest.skipUnless((SCOPE/'algorithm_a/scalar-copy-proof.json').is_file(),
                      'Local retail/compiler proofs are not distributed')
 @unittest.skip("Original game/compiler review evidence stays in the originating local repository.")
 def test_retained_native_proof_without_recompilation(self):
  folder = SCOPE / 'func_01EED628'
  source = (folder / 'baseline.c').read_text(encoding='utf-8')
  generated = generate(self.rules, source)
  self.assertIsNotNone(generated)
  rows = [json.loads(line) for line in (folder / 'a-attempts.jsonl').read_text(encoding='utf-8').splitlines()]
  row = next(r for r in rows if r.get('label') == 'generic-scalar-copy-temp-generated-proof')
  snapshot = Path(row['snapshot']); blob = snapshot.read_bytes()
  self.assertEqual(hashlib.sha256(blob).hexdigest(), row['sourceHash'])
  self.assertTrue(row['strict']['passed'])
  self.assertEqual(hashlib.sha256(snapshot.with_suffix('.o').read_bytes()).hexdigest(),row['objectHash'])
  retained_result = json.loads(snapshot.with_name('result.json').read_text(encoding='utf-8'))
  self.assertEqual(retained_result['sourceHash'],row['sourceHash'])
  self.assertTrue(retained_result['byteMatch'])
  self.assertEqual(retained_result['compiledSize'],384)
  actual_ast = self.rules.generator.visit(self.rules.parse(generated)[0])
  strict_ast = self.rules.generator.visit(self.rules.parse(blob.decode('utf-8-sig'))[0])
  self.assertEqual(actual_ast, strict_ast)
  proof = {'status':'hardened-export-only-no-recompile', 'guardCases':len(CASES)+len(EXTRA),
   'overlapPairs':16, 'inputASTUnmutated':True, 'baselineHash':hashlib.sha256((folder/'baseline.c').read_bytes()).hexdigest(),
   'generatedHash':hashlib.sha256(generated.encode()).hexdigest(), 'strictSnapshot':str(snapshot),
   'strictSourceHash':row['sourceHash'], 'objectHash':row['objectHash'],
   'strict':row['strict'], 'sameCanonicalASTAndFlags':True,
   'newCompilerRuns':0, 'retainedResultHash':hashlib.sha256(snapshot.with_name('result.json').read_bytes()).hexdigest(), 'flags':__import__('re').findall(r'/\* cflags:.*?\*/',generated)}
  self.assertEqual(proof['flags'], __import__('re').findall(r'/\* cflags:.*?\*/',blob.decode('utf-8-sig')))
  retained = json.loads((SCOPE/'algorithm_a/scalar-copy-proof.json').read_text(encoding='utf-8'))
  self.assertEqual(proof['generatedHash'],'71d0ca599d7c6207f73ebf43945d1161bd30f6aad2664138f11c5d4b300aaa0c')
  for key in ('baselineHash','generatedHash','strictSourceHash','objectHash','retainedResultHash','strict'):
   self.assertEqual(proof[key],retained[key])
  self.assertEqual(generated.encode('utf-8'),blob)

 def test_dispatch_is_bounded_and_does_not_repeat_its_output(self):
  source=BASE.replace('BODY',COPY)
  rows=children(source,self.rules)
  self.assertEqual(len(rows),1)
  self.assertEqual(rows[0][0],LABEL)
  self.assertEqual(rows[0][1],generate(self.rules,source))
  self.assertEqual(children(rows[0][1],self.rules),[])
  self.assertEqual(children('void f(void){}',self.rules),[])
  self.assertEqual(children('broken C {',self.rules),[])

 def test_only_longest_contiguous_run_is_changed(self):
  shorter='*(s32 *)d=*(s32 *)s;*(s32 *)((s8 *)d+4)=*(s32 *)((s8 *)s+4);'
  longer=shorter+'*(s32 *)((s8 *)d+8)=*(s32 *)((s8 *)s+8);'
  source=BASE.replace('BODY','if(d){'+shorter+'} if(s){'+longer+'}')
  output=children(source,self.rules)[0][1]
  _,fn=self.rules.parse(output)
  branches=[n for n in fn.body.block_items if isinstance(n,self.rules.a.If)]
  self.assertEqual(len(branches[0].iftrue.block_items),2)
  self.assertEqual(len(branches[1].iftrue.block_items),6)

 def test_unsigned_words_and_constant_byte_offsets_preserve_exact_types(self):
  source='''void f(u8 *d,u8 *s) {
   *(u32 *)(d-4)=(u32)*(u32 *)(s-4);
   *(u32 *)(4U+d)=*(u32 *)(s+04);
  }'''
  output=children(source,self.rules)[0][1]
  self.assertIn('u32 rac4_copy_word;',output)
  self.assertNotIn('s32 rac4_copy_word;',output)
  _,original=self.rules.parse(source);_,generated=self.rules.parse(output)
  self.assertEqual(self.rules.generator.visit(original.decl),self.rules.generator.visit(generated.decl))
  old_assignments=original.body.block_items
  new_assignments=generated.body.block_items[1:]
  for index,old in enumerate(old_assignments):
   self.assertEqual(self.rules.generator.visit(new_assignments[index*2].rvalue),
                    self.rules.generator.visit(old.rvalue))
   self.assertEqual(self.rules.generator.visit(new_assignments[index*2+1].lvalue),
                    self.rules.generator.visit(old.lvalue))

 def test_generated_ast_preserves_real_memory_operation_trace_under_overlap(self):
  # Interpret the actual generated AST. Forward overlap feeds earlier stores
  # into later reads, unlike an aggregate preload of the source words.
  source=BASE.replace('BODY',' '.join(
   '*(s32 *)((s8 *)d+%d)=*(s32 *)((s8 *)s+%d);'%(i*4,i*4) for i in range(6)))
  generated=children(source,self.rules)[0][1]
  a=self.rules.a
  def execute(text,source_address,destination_address):
   _,fn=self.rules.parse(text);memory={i*4:i for i in range(16)}
   locals={'s':source_address,'d':destination_address};trace=[]
   def value(n):
    if isinstance(n,a.ID):return locals[n.name]
    if isinstance(n,a.Constant):return int(n.value,0)
    if isinstance(n,a.Cast):return value(n.expr)
    if isinstance(n,a.BinaryOp) and n.op=='+':return value(n.left)+value(n.right)
    if isinstance(n,a.UnaryOp) and n.op=='*':
     address=value(n.expr);trace.append(('read',address,memory[address]));return memory[address]
    self.fail('Unexpected generated expression '+type(n).__name__)
   for statement in fn.body.block_items:
    if isinstance(statement,a.Decl):continue
    self.assertIsInstance(statement,a.Assignment)
    result=value(statement.rvalue)
    if isinstance(statement.lvalue,a.ID):locals[statement.lvalue.name]=result
    else:
     address=value(statement.lvalue.expr);memory[address]=result;trace.append(('write',address,result))
   return memory,trace
  for source_index in range(4):
   for destination_index in range(4):
    with self.subTest(source=source_index,destination=destination_index):
     self.assertEqual(execute(source,source_index*4,destination_index*4),
                      execute(generated,source_index*4,destination_index*4))

if __name__ == '__main__':
 unittest.main(verbosity=2)
