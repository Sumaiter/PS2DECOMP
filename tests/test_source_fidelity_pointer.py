"""Diagnostic coverage; no compiler, game binary or semantic approval required."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

import reviewed_seeds
import source_fidelity


HEADERS = (
    'typedef int Index;\ntypedef unsigned long Word;\ntypedef unsigned char Byte;\ntypedef Byte *Pointer;\n',
    'typedef short Index;\ntypedef unsigned int Word;\ntypedef unsigned char Byte;\ntypedef Byte *Pointer;\n',
)


def examples():
    return {
        'assignment': 'Pointer example(Index n){Pointer p;p=n*32+16384;return p;}',
        'initializer': 'Pointer example(Index n){Pointer p=n*32+16384;return p;}',
        'pointer-arithmetic': 'extern Byte table[];Pointer example(Index n){Pointer p=table+n*32;return p;}',
        'integer-arithmetic': 'Word example(Index n){Word p=n*32+16384;return p;}',
        'null': 'Pointer example(void){Pointer p=(Word)(2-2);return p;}',
    }


class PointerDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'include').mkdir()
        self.header(HEADERS[0])

    def header(self, value):
        (self.root / 'include/common.h').write_text(value, encoding='utf-8')

    def inspect(self, body):
        result = source_fidelity.inspect('#include "common.h"\n' + body, self.root)
        self.assertTrue(result['astAnalyzed'], result)
        self.assertIsNone(result['integerPointerAnalysisLimitation'], result)
        return result

    def count(self, body, expected):
        result = self.inspect(body)
        self.assertEqual(len(result['integerPointerConversions']), expected, result)
        return result

    def test_two_header_contexts(self):
        for header in HEADERS:
            self.header(header)
            for name, body in examples().items():
                with self.subTest(header=header, name=name):
                    self.count(body, int(name in ('assignment', 'initializer')))

    def test_casts_constants_and_parameter_destination(self):
        self.count('void example(Pointer p,Index n){p=(Word)n*32+16384;p=(Word)(3+4);p=16384;}', 3)

    def test_null_and_uncertain_constant_expressions(self):
        self.count('void example(void){Pointer p;p=0;p=-0;p=0U;p=(Word)0;p=(Word)(1-1);p=(unsigned char)256;p=(unsigned int)-1+1;p=1/2;p=1<<16;p=(0U > -1);p=(0U-1+1);p=~0;p=(signed char)-1U;}', 0)

    def test_unknown_expressions_and_types(self):
        self.count('int callback(void);void example(void){Pointer p;p=callback();p=(Word)callback();p=sizeof(int);p=(int)0.5;}', 0)
        self.header('typedef float Index;\ntypedef unsigned int Word;\ntypedef void *Pointer;\n')
        self.count('void example(Index n){Pointer p;p=n*32+16384;}', 0)

    def test_attribute_modes_are_not_plain_integer_typedefs(self):
        self.header('typedef int Wide __attribute__((mode(TI)));\ntypedef void *Pointer;\n')
        self.count('void example(Wide n){Pointer p=n*32+16384;}', 0)
        self.count('void example(void){int n __attribute__((mode(TI)));Pointer p=n*32+16384;}', 0)

    def test_real_pointer_expressions(self):
        self.count('void example(Pointer base,Index n){Pointer p;p=base+n;p=&n;p=(Pointer)0;p=base;}', 0)

    def test_lexical_shadowing_and_typedef_capture(self):
        self.count('void example(Index n){Pointer p;{int p;p=n*32+16384;}p=n*32+16384;}', 1)
        self.count('void example(Index n){Pointer p;{float n;p=n*32+16384;}p=n*32+16384;}', 1)
        self.count('void example(Index n){typedef Index Saved;{typedef float Index;Saved x;Pointer p=x*32+16384;Index f;Pointer q=f*32+16384;}}', 1)
        self.count('void example(Index n){typedef int Pointer;Pointer p=n*32+16384;}', 0)
        self.count('void example(Index n){for(float n=0;n<1;n=n+1){Pointer p=n*32+16384;}Pointer p=n*32+16384;}', 1)

    def test_complex_destinations_and_global_storage_skipped(self):
        self.count('extern Pointer global;struct S{Pointer p;};void example(Index n,struct S *s){global=n*32+16384;s->p=n*32+16384;}', 0)

    def test_existing_violations_and_eligibility_unchanged(self):
        result = self.count('void example(void){Pointer p=(Pointer)1234;}', 0)
        self.assertEqual(result['violations'], [dict(kind='literal-pointer-address', expression='(Pointer) 1234', value=1234)])
        source = '#include "common.h"\nvoid func_00000010(Index n){Pointer p;p=n*32+16384;}'
        result = source_fidelity.inspect(source, self.root)
        self.assertEqual(result['violations'], [])
        self.assertEqual(len(result['integerPointerConversions']), 1)
        self.assertFalse(result['semanticApproval'])
        reviewed_seeds._ast(source, self.root, 'func_00000010')

    def test_diagnostic_failure_does_not_change_old_ast_gate(self):
        source = '#include "common.h"\nvoid func_00000010(Index n){Pointer p;p=n*32+16384;}'
        with patch('integer_pointer_diagnostics.inspect', side_effect=ValueError('test limitation')):
            result = source_fidelity.inspect(source, self.root)
            self.assertTrue(result['astAnalyzed'])
            self.assertEqual(result['violations'], [])
            self.assertIsNone(result['analysisLimitation'])
            self.assertEqual(result['integerPointerAnalysisLimitation'], 'test limitation')
            reviewed_seeds._ast(source, self.root, 'func_00000010')

    def test_published_compiler_sources_and_relocation_counterexamples(self):
        data = json.loads((Path(__file__).parent / 'evidence/integer-pointer-diagnostics.json').read_text(encoding='utf-8'))
        self.assertTrue(data['syntheticSourcesOnly'])
        self.assertFalse(data['semanticApproval'])
        self.assertEqual(data['compilerRuns'], 10)
        replay = data['motivatingLocalReview']['diagnosticReplay']
        self.assertEqual(replay['diagnosticCount'], 1)
        self.assertTrue(replay['existingViolationsUnchanged'])
        self.assertEqual(replay['actualObjectRelocations'], [])
        self.assertEqual(len(replay['placementEvidence']), 2)
        self.assertEqual(replay['gameCompilerRuns'], 0)
        self.assertFalse(replay['semanticApproval'])
        records = {(r['context'], r['case']): r for r in data['cases']}
        self.assertEqual(len(records), 10)
        for context, header in zip(('int-long', 'short-int'), HEADERS):
            for name, body in examples().items():
                record = records[(context, name)]
                source = '#include "common.h"\n' + body + '\n'
                self.assertEqual(record['headerHash'], hashlib.sha256(header.encode()).hexdigest())
                self.assertEqual(record['sourceLFHash'], hashlib.sha256(source.encode()).hexdigest())
                self.assertEqual(record['diagnosticCount'], int(name in ('assignment', 'initializer')))
                if name == 'pointer-arithmetic':
                    self.assertTrue(record['relocations'])
                    self.assertEqual({r['symbol'] for r in record['relocations']}, {'table'})
                else:
                    self.assertEqual(record['relocations'], [])


if __name__ == '__main__':
    unittest.main()
