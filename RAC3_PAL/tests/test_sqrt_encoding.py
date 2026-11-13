"""EE source-field, unrelated-format and strict raw-listing counterexamples."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from RAC3_PAL import division_padding, sqrt_encoding as sqrt
from RAC3_PAL.rac3_engine import Engine


class SqrtEncodingTests(unittest.TestCase):
    def test_every_source_and_destination_uses_ft_with_zero_fs(self):
        for fd in range(32):
            for ft in range(32):
                word = 0x46000004 | ft << 16 | fd << 6
                self.assertEqual(sqrt.fields(word), (fd, ft))

    def test_generic_mips_fs_forms_other_formats_and_reserved_fields_are_not_ee_recovery(self):
        for word in (0x46000804, 0x46010044 | 1 << 11, 0x46210044, 0x46810044,
                     0x46010043, 0x46010046, 0x46010056, 0x4a010044, -1, 2**32):
            self.assertIsNone(sqrt.fields(word), hex(word))
        for fs in range(1, 32):
            self.assertIsNone(sqrt.fields(0x46000004 | fs << 11))

    def test_raw_c1_and_word_renderings_keep_word_address_and_all_other_lines(self):
        for mnemonic, operand in (('c1', '0x10044'), ('.word', '0x46010044')):
            # c1's operand omits the COP1 major bits; Rabbitizer emits this form.
            line = '/* 00001000 46010044 */  ' + mnemonic + ' ' + operand + '\n'
            output, evidence = sqrt.normalize(line)
            self.assertEqual(output, '/* 00001000 46010044 */  sqrt.s $f1, $f1\n')
            self.assertEqual(evidence[0]['word'], '46010044')
            self.assertEqual(evidence[0]['address'], '00001000')

    def test_mismatched_comments_operands_and_other_instruction_spellings_are_untouched(self):
        for line in ('/* 00001000 46010044 */ c1 0x20044\n',
                     '/* 00001000 46010044 */ .word 0x46020044\n',
                     '/* 00001000 46000804 */ c1 0x804\n',
                     '/* 00001000 46010044 */ sqrt.s $f1, $f1\n',
                     '/* 00001000 46010044 */ c1 0x10044, $f1\n',
                     '/* 00001000 46010044 */\nc1 0x10044\n',
                     'c1 0x10044\n'):
            self.assertEqual(sqrt.normalize(line), (line, []))
        # Other adapters retain their own instruction-format policy.
        import rough
        line = '/* 00001000 46010044 */ c1 0x10044\n'
        self.assertEqual(rough.normalize_raw_cop1(line), (line, []))

    def test_padding_counts_accept_nonzero_ft_and_reject_generic_nonzero_fs(self):
        import struct
        raw = lambda *items: struct.pack('<' + 'I' * len(items), *items)
        self.assertEqual(division_padding.native_sites(raw(0, 0, 0x460c0004, 0x03e00008, 0))[0]['count'], 2)
        self.assertIsNone(division_padding.native_sites(raw(0x46006004)))

    def test_only_rac3_decompile_adds_decoding_evidence_before_m2c(self):
        with tempfile.TemporaryDirectory() as directory:
            name = 'func_L01_00001000'
            listing = 'glabel ' + name + '\n/* 00001000 460C0004 */ c1 0xC0004\n'
            engine = SimpleNamespace(root=Path(directory), contracts={}, asm=lambda n: listing,
                                     assert_access=lambda n, owner: None)
            padding = dict(status='not-applicable')
            with patch('rough.tool', return_value=Path('synthetic-m2c.py')), \
                    patch('rough.forwarding_evidence', return_value=dict(findings=[], warnings=[])), \
                    patch.object(division_padding, 'inspect', return_value=padding), \
                    patch('local_process.run', return_value=SimpleNamespace(returncode=0,
                        stdout='float ' + name + '(float x) { return sqrtf(x); }', stderr='')) as run:
                result = Engine.decompile(engine, name)
            folder = Path(result['directory'])
            self.assertEqual(folder.joinpath('original.s').read_text(), listing)
            self.assertIn('sqrt.s $f0, $f12', folder.joinpath('input.s').read_text())
            self.assertEqual(result['instructionDecodings'][0]['word'], '460C0004')
            self.assertEqual(run.call_args.kwargs['timeout'], 45)


if __name__ == '__main__':
    unittest.main()
