"""BNE-to-zero operands retain raw registers, conditions and local targets."""
import struct
from types import SimpleNamespace
import unittest
import rough

from RAC3_PAL.branch_operands import bnez_labels, decode_bnez, decode_integer, integer_labels
from RAC3_PAL.rac3_engine import Engine


def words(*items):
    return struct.pack('<' + 'I' * len(items), *items)


class BnezOperandTests(unittest.TestCase):
    def test_register_and_signed_displacements_are_decoded_without_abi_renaming(self):
        for register, name in ((2, 'v0'), (8, 't0'), (16, 's0'), (31, 'ra')):
            for displacement in (0, 1, -1, -20, 32767, -32768):
                word = 0x14000000 | register << 21 | (displacement & 0xffff)
                self.assertEqual(decode_bnez(word, 0x1000),
                                 (name, (0x1004 + displacement * 4) & 0xffffffff))

    def test_other_conditions_register_pairs_and_likely_encodings_stay_outside_recovery(self):
        for word in (0x14000001, 0x14410001, 0x10400001, 0x54400001,
                     0x04400001, 0x45000001, -1, True, 1 << 32):
            self.assertIsNone(decode_bnez(word, 0x1000))
        for address in (None, -4, 0x1001, 1 << 32, True):
            self.assertIsNone(decode_bnez(0x14400001, address))

    def test_integer_conditions_register_order_and_likely_flags_are_preserved(self):
        for word, mnemonic, registers in (
                (0x10000001, 'b', ()), (0x10400001, 'beqz', ('v0',)),
                (0x14400001, 'bnez', ('v0',)), (0x10430001, 'beq', ('v0', 'v1')),
                (0x14030001, 'bne', ('zero', 'v1')), (0x50430001, 'beql', ('v0', 'v1')),
                (0x54430001, 'bnel', ('v0', 'v1')), (0x18400001, 'blez', ('v0',)),
                (0x1c400001, 'bgtz', ('v0',)), (0x58400001, 'blezl', ('v0',)),
                (0x5c400001, 'bgtzl', ('v0',)), (0x04400001, 'bltz', ('v0',)),
                (0x04410001, 'bgez', ('v0',)), (0x04420001, 'bltzl', ('v0',)),
                (0x04430001, 'bgezl', ('v0',))):
            with self.subTest(mnemonic=mnemonic):
                self.assertEqual(decode_integer(word, 0x1000), (mnemonic, registers, 0x1008))
                self.assertEqual(integer_labels(words(word, 0, 0x03e00008, 0), 0x1000),
                                 {0: (mnemonic, registers, 0x1008)})

    def test_link_branches_reserved_register_fields_and_other_opcodes_are_not_recovered(self):
        for word in (0x04500001, 0x04510001, 0x04520001, 0x04530001,
                     0x18410001, 0x1c410001, 0x58410001, 0x5c410001,
                     0x08000400, 0x0c000400, 0x45000001, 0x49000001, 0x03e00008):
            self.assertIsNone(decode_integer(word, 0x1000))

    def test_local_loop_and_forward_branch_targets(self):
        raw = words(0x14400003, 0, 0x1440fffd, 0, 0x03e00008, 0)
        self.assertEqual(bnez_labels(raw, 0x1000), {0: ('v0', 0x1010), 8: ('v0', 0x1000)})

    def test_incomplete_boundaries_and_control_delay_slots_are_not_recovered(self):
        for raw in (b'', b'\x00', words(0x14400001), words(0x14400001, 0),
                    words(0x14400010, 0, 0x03e00008, 0),
                    words(0x1440fff0, 0, 0x03e00008, 0),
                    words(0x14400001, 0x14400001, 0x03e00008, 0),
                    words(0x14400001, 0x0c001000, 0x03e00008, 0),
                    words(0x14400001, 0x0000000d, 0x03e00008, 0),
                    words(0x03e00008, 0x14400001, 0x03e00008, 0)):
            self.assertEqual(bnez_labels(raw, 0x1000), {})
        raw = words(0x14400001, 0, 0x03e00008, 0)
        for start in (True, -4, 0x1001, 0xfffffffc):
            self.assertEqual(bnez_labels(raw, start), {})

    def test_adapter_canonicalizes_only_the_operand_and_preserves_delay_words(self):
        raw = words(0x14400003, 0x24630001, 0, 0, 0x03e00008, 0)
        engine = SimpleNamespace(place=lambda name: ('level_02', '.text', 0x1000, len(raw)),
                                 disc_bytes=lambda place: raw, _symbols={})
        output = Engine.asm(engine, 'func_L02_00001000')
        self.assertIn('/* 00001000 14400003 */  bnez $v0, .L00001010', output)
        self.assertIn('/* 00001004 24630001 */', output)
        self.assertEqual(output.count('.L00001010:'), 1)
        self.assertNotIn('. + 4 +', output)

    def test_existing_ee_register_alias_normalization_remains_after_listing_recovery(self):
        raw = words(0x15000001, 0, 0x03e00008, 0)
        engine = SimpleNamespace(place=lambda name: ('level_02', '.text', 0x1000, len(raw)),
                                 disc_bytes=lambda place: raw, _symbols={})
        output = Engine.asm(engine, 'func_L02_00001000')
        self.assertIn('bnez $t0, .L00001008', output)
        normalized, aliases = rough.normalize_ee_registers(output)
        self.assertIn('bnez $a4, .L00001008', normalized)
        self.assertEqual(aliases, [dict(physicalRegister=8, listing='$t0', m2c='$a4')])


if __name__ == '__main__':
    unittest.main()
