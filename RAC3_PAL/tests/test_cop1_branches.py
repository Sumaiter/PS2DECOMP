"""Exact COP1 encodings, signed targets and delay/control counterexamples."""
import struct
from types import SimpleNamespace
import unittest

from RAC3_PAL.cop1_branches import decode, labels
from RAC3_PAL.rac3_engine import Engine


def words(*items):
    return struct.pack('<' + 'I' * len(items), *items)


class Cop1BranchTests(unittest.TestCase):
    def test_false_true_and_likely_forms_preserve_their_condition_and_delay_behavior(self):
        for flag, mnemonic in enumerate(('bc1f', 'bc1t', 'bc1fl', 'bc1tl')):
            with self.subTest(mnemonic=mnemonic):
                self.assertEqual(decode(0x45000003 | flag << 16, 0x1000), (mnemonic, 0x1010))

    def test_signed_displacements_and_32_bit_pc_wrap(self):
        for immediate, target in ((0, 0x1004), (1, 0x1008), (0xffff, 0x1000),
                                  (0xfffe, 0xffc), (0x7fff, 0x21000), (0x8000, 0xfffe1004)):
            self.assertEqual(decode(0x45000000 | immediate, 0x1000), ('bc1f', target))
        self.assertEqual(decode(0x45000000, 0xfffffffc), ('bc1f', 0))

    def test_other_coprocessors_reserved_condition_fields_and_fp_ops_are_not_branches(self):
        for word in (0x41000003, 0x49000003, 0x45040003, 0x45100003,
                     0x45200003, 0x44800003, 0x46000003, 0x10000003, 0x03e00008,
                     -1, 1 << 32, True):
            self.assertIsNone(decode(word, 0x1000))
        for address in (-4, 1 << 32, 0x1001, True, None):
            self.assertIsNone(decode(0x45000003, address))

    def test_local_forward_backward_and_multiple_targets(self):
        raw = words(0x45000003, 0, 0x4501fffd, 0, 0x03e00008, 0)
        self.assertEqual(labels(raw, 0x1000), {0: ('bc1f', 0x1010), 8: ('bc1t', 0x1000)})

    def test_all_four_forms_require_complete_local_control_flow(self):
        for flag in range(4):
            raw = words(0x45000001 | flag << 16, 0, 0x03e00008, 0)
            self.assertEqual(labels(raw, 0x1000)[0][1], 0x1008)
        for raw in (words(0x45000001), words(0x45000001, 0),
                    words(0x45000020, 0, 0x03e00008, 0),
                    words(0x45008000, 0, 0x03e00008, 0),
                    words(0x45000001, 0x03e00008, 0x03e00008, 0),
                    words(0x03e00008, 0x45000001, 0x03e00008, 0),
                    words(0x45000001, 0x0c001000, 0x03e00008, 0),
                    words(0x45000001, 0x0000000d, 0x03e00008, 0),
                    words(0x45000003, 0, 0x45040001, 0, 0x03e00008, 0),
                    words(0x45000001, 0, 0x45000000, 0)):
            self.assertEqual(labels(raw, 0x1000), {})

    def test_invalid_windows_do_not_create_labels(self):
        for raw in (b'', b'\x00', bytearray(words(0x45000001, 0, 0x03e00008, 0))):
            self.assertEqual(labels(raw, 0x1000), {})
        raw = words(0x45000001, 0, 0x03e00008, 0)
        for start in (-4, True, 0x1001, 0xfffffffc):
            self.assertEqual(labels(raw, start), {})

    def test_adapter_uses_raw_word_instead_of_rabbitizer_operand_spelling(self):
        raw = words(0x45000003, 0, 0x24020001, 0, 0x03e00008, 0)
        engine = SimpleNamespace(place=lambda name: ('level_02', '.text', 0x1000, len(raw)),
                                 disc_bytes=lambda place: raw, _symbols={})
        output = Engine.asm(engine, 'func_L02_00001000')
        self.assertIn('/* 00001000 45000003 */  bc1f .L00001010', output)
        self.assertEqual(output.count('.L00001010:'), 1)
        self.assertIn('/* 00001008 24020001 */', output)
        self.assertNotIn('. + 4 +', output)

    def test_integer_branch_and_jal_handling_are_retained(self):
        raw = words(0x10850003, 0, 0x0c000800, 0, 0x03e00008, 0)
        engine = SimpleNamespace(place=lambda name: ('level_02', '.text', 0x1000, len(raw)),
                                 disc_bytes=lambda place: raw, _symbols={'level_02': {0x2000: 'func_L02_00002000'}})
        output = Engine.asm(engine, 'func_L02_00001000')
        self.assertIn('jal func_L02_00002000', output)
        self.assertEqual(output.count('.L00001010:'), 1)


if __name__ == '__main__':
    unittest.main()
