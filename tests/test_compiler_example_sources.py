"""Reproduce published synthetic source hashes; no compiler or ROM needed."""
from pathlib import Path
import hashlib
import importlib.util
import json
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('compiler_rule_examples',ROOT/'tests/compile_rule_examples.py')
examples=importlib.util.module_from_spec(spec);spec.loader.exec_module(examples)


class CompilerExampleSources(unittest.TestCase):
    def test_every_published_pair_comes_from_current_generators_and_header(self):
        data=json.loads((ROOT/'tests/evidence/private-local-compiler.json').read_text(encoding='utf-8'))
        self.assertTrue(data['syntheticSourcesOnly']);self.assertFalse(data['retailComparison'])
        self.assertEqual(data['compilerRuns'],16)
        records={(r['context'],r['rule']):r for r in data['cases']}
        self.assertEqual(len(records),8)
        for context,leaf in [('float-alias','float'),('double-alias','double')]:
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder);(root/'include').mkdir()
                header='typedef '+leaf+' Real;\ntypedef int Status;\n'
                (root/'include/common.h').write_text(header,encoding='utf-8')
                for name,before,after in examples.examples(root):
                    record=records[(context,name)]
                    self.assertEqual(record['headerHash'],hashlib.sha256(header.encode()).hexdigest())
                    for item,text in zip(record['pair'],(before,after)):
                        self.assertEqual(item['sourceLFHash'],hashlib.sha256(text.encode()).hexdigest())
                    self.assertTrue(record['sameExternalRelocationInventory'])


if __name__=='__main__':unittest.main()
