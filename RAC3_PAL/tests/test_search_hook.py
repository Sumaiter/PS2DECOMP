"""Compose independent root proofs before helper syntax leaves the AST subset."""
import unittest
from unittest.mock import patch

from RAC3_PAL.rac3_engine import Engine
from RAC3_PAL import extern_sizes, quadword_zero, symbolic_memory


class SearchHookTests(unittest.TestCase):
    def candidates(self, hints):
        source = '#include "common.h"\nvoid func_00100000(void *p) {}\n'
        recovered = '#include "common.h"\n#include "vu0.h"\nvoid func_00100000(void *p) {qzero(p);}\n'
        with patch.object(quadword_zero, 'children', return_value=[('zero', recovered)]), \
                patch.object(symbolic_memory, 'children', return_value=[]), \
                patch.object(extern_sizes, 'children', return_value=[('size', hints + source)]):
            return source, recovered, list(Engine.additional_search_children(object(), 'func_00100000', source, object()))

    def test_helper_and_metadata_proofs_are_composed_from_the_unchanged_root(self):
        prefix = '__asm__(".extern D_00123450_sda, 4");\n'
        source, recovered, rows = self.candidates(prefix)
        self.assertEqual(rows, [('zero', recovered), ('size', prefix + source),
                                ('zero-with-proven-gp-size', prefix + recovered)])

    def test_arbitrary_assembly_is_not_composed_with_a_helper(self):
        for prefix in ('__asm__("nop");\n', '__asm__(".extern D_00123450, 8");\n',
                       '__asm__(".extern not_a_data_symbol, 4");\n'):
            with self.subTest(prefix=prefix):
                self.assertEqual(len(self.candidates(prefix)[2]), 2)
