"""Translation-unit diagnostics must inspect helpers without broadening search."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

from search_rules import Variants
import reviewed_seeds
import source_fidelity

HEADERS = (
    'typedef int Scalar;\ntypedef float Real;\ntypedef unsigned char Byte;\ntypedef Byte *Pointer;\n',
    'typedef short Scalar;\ntypedef double Real;\ntypedef unsigned char Byte;\ntypedef void *Pointer;\n',
)

def examples():
    return {
        'literal-in-helper': 'static inline Pointer helper(void){return (Pointer)16384;} Pointer example(void){return helper();}',
        'integer-in-helper': 'static inline Pointer helper(Scalar n){Pointer p=n*32+16384;return p;} Pointer example(Scalar n){return helper(n);}',
        'external-table': 'extern Byte table[];static inline Pointer helper(Scalar n){return table+n;} Pointer example(Scalar n){return helper(n);}',
        'floating-helper': 'static inline Real helper(Real x){Real bias;if(x>(Real)0){bias=(Real)0.5;bias=x+bias;}else{bias=(Real)0.5;bias=x-bias;}return bias;} Real example(Real x){return helper(x);}',
    }


class HelperDiagnostics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'include').mkdir()
        self.header(HEADERS[0])

    def header(self, text):
        (self.root / 'include/common.h').write_text(text, encoding='utf-8')

    def inspect(self, body):
        result = source_fidelity.inspect('#include "common.h"\n' + body, self.root)
        self.assertFalse(result['semanticApproval'])
        return result

    def test_helper_bodies_in_two_header_contexts(self):
        for header in HEADERS:
            self.header(header)
            for name, body in examples().items():
                with self.subTest(header=header, case=name):
                    result = self.inspect(body)
                    self.assertTrue(result['astAnalyzed'], result)
                    self.assertIsNone(result['analysisLimitation'])
                    self.assertIsNone(result['integerPointerAnalysisLimitation'])
                    self.assertEqual(len(result['violations']), int(name == 'literal-in-helper'))
                    self.assertEqual(len(result['integerPointerConversions']), int(name == 'integer-in-helper'))

    def test_function_local_types_do_not_leak(self):
        result = self.inspect('static Pointer one(Scalar n){Pointer p=n+1;return p;} static Real two(Real n){Real p=n+1;return p;} Real example(Real n){return two(n);}')
        self.assertTrue(result['astAnalyzed'], result)
        self.assertEqual(len(result['integerPointerConversions']), 1)
        self.assertEqual(result['integerPointerConversions'][0]['destination'], 'p')
        result = self.inspect('static void one(Scalar n){typedef int Pointer;Pointer p=n+1;} Pointer two(Scalar n){Pointer p=n+1;return p;}')
        self.assertTrue(result['astAnalyzed'], result)
        self.assertEqual(len(result['integerPointerConversions']), 1)

    def test_nested_helper_cast_is_not_hidden_by_safe_entry(self):
        result = self.inspect('typedef Pointer Alias;static Pointer a(void){return (Alias)-16;}static Pointer b(void){return a();}Pointer example(void){return b();}')
        self.assertTrue(result['astAnalyzed'], result)
        self.assertEqual([v['value'] for v in result['violations']], [-16])

    def test_literal_casts_follow_helper_local_aliases_and_shadowing(self):
        result = self.inspect('static Scalar one(void){typedef int Pointer;return (Pointer)16384;} Scalar example(void){return one();}')
        self.assertTrue(result['astAnalyzed'], result)
        self.assertEqual(result['violations'], [])
        result = self.inspect('static void *one(void){typedef void *Local;return (Local)16384;} void *example(void){return one();}')
        self.assertTrue(result['astAnalyzed'], result)
        self.assertEqual([v['value'] for v in result['violations']], [16384])

    def test_literal_alias_binding_is_captured_and_does_not_leak(self):
        result = self.inspect('typedef Pointer Saved;static void *one(void){typedef int Pointer;return (Saved)16384;} Pointer example(void){return (Pointer)32;}')
        self.assertTrue(result['astAnalyzed'], result)
        self.assertEqual([v['value'] for v in result['violations']], [16384, 32])
        result = self.inspect('static Scalar one(void){typedef int Pointer;return (Pointer)1;} Pointer example(void){return (Pointer)32;}')
        self.assertEqual([v['value'] for v in result['violations']], [32])

    def test_search_keeps_exactly_one_function_guard(self):
        rules = Variants(self.root)
        for body in examples().values():
            source = '#include "common.h"\n' + body
            with self.subTest(body=body):
                with self.assertRaisesRegex(ValueError, 'Genau eine Funktion'):
                    rules.parse(source)
                with self.assertRaisesRegex(ValueError, 'Genau eine Funktion'):
                    list(rules.generate(source))
                rejection = 'Source fidelity' if '(Pointer)16384' in body else 'Genau eine Funktion'
                with self.assertRaisesRegex(ValueError, rejection):
                    reviewed_seeds._ast(source, self.root, 'example')

    def test_preprocessor_and_flag_guards_still_apply_to_unit(self):
        body = examples()['external-table']
        for prefix in ('#include "other.h"\n', '#define ADDRESS 1234\n', '#if 1\n', '/* cflags: -O2 */\n/* cflags: -O1 */\n', '\\'+'\n'):
            with self.subTest(prefix=prefix):
                result = self.inspect(prefix + body)
                self.assertFalse(result['astAnalyzed'])
                self.assertTrue(result['analysisLimitation'])

    def test_scalar_helpers_never_grant_semantic_approval(self):
        result = self.inspect('static Real lower(Real x){return x>(Real)8?x:(Real)8;} static Real upper(Real x){return x>(Real)160?(Real)160:x;} static Real rounding(Real x){Real b;if(x>(Real)0){b=(Real)0.5;b=x+b;}else{b=(Real)0.5;b=x-b;}return b;} Scalar example(Real x){return (Scalar)rounding(upper(lower(x)));}')
        self.assertTrue(result['astAnalyzed'], result)
        self.assertEqual(result['violations'], [])
        self.assertEqual(result['integerPointerConversions'], [])
        self.assertFalse(result['semanticApproval'])

    def test_data_only_units_are_diagnostics_not_search_inputs(self):
        result = self.inspect('Pointer bad=(Pointer)16384;')
        self.assertTrue(result['astAnalyzed'], result)
        self.assertEqual(len(result['violations']), 1)
        with self.assertRaisesRegex(ValueError, 'Genau eine Funktion'):
            Variants(self.root).parse('Pointer bad=(Pointer)16384;')

    def test_real_compiler_evidence_and_relocation_counterexamples(self):
        evidence = json.loads((Path(__file__).parent / 'evidence/helper-source-diagnostics.json').read_text(encoding='utf-8'))
        self.assertTrue(evidence['syntheticSourcesOnly'])
        self.assertFalse(evidence['semanticApproval'])
        self.assertFalse(evidence['sourceRewriting'])
        self.assertEqual(evidence['compilerRuns'], 8)
        records = {(r['context'], r['case']): r for r in evidence['cases']}
        self.assertEqual(len(records), 8)
        for context, header in zip(('int-float-byte-pointer', 'short-double-void-pointer'), HEADERS):
            self.header(header)
            for name, body in examples().items():
                with self.subTest(context=context, case=name):
                    source = '#include "common.h"\n' + body + '\n'
                    record = records[(context, name)]
                    self.assertEqual(record['headerHash'], hashlib.sha256(header.encode()).hexdigest())
                    self.assertEqual(record['sourceLFHash'], hashlib.sha256(source.encode()).hexdigest())
                    result = self.inspect(body)
                    self.assertEqual(len(result['violations']), record['violations'])
                    self.assertEqual(len(result['integerPointerConversions']), record['integerPointerConversions'])
                    if name == 'external-table':
                        self.assertEqual({r['symbol'] for r in record['relocations']}, {'table'})
                        self.assertEqual({r['type'] for r in record['relocations']}, {5, 6})
                    elif name != 'floating-helper':
                        self.assertEqual(record['relocations'], [])
        motivating = evidence['motivatingReview']
        self.assertEqual((motivating['inlineHelpers'], motivating['exportedFunctions']), (3, 1))
        self.assertEqual(motivating['completeFunctionBytes'], 172)
        self.assertFalse(motivating['previousAstAnalyzed'])
        self.assertTrue(motivating['newAstAnalyzed'])
        self.assertTrue(motivating['sourceObjectUnchanged'])
        self.assertFalse(motivating['semanticApproval'])


if __name__ == '__main__':
    unittest.main()
