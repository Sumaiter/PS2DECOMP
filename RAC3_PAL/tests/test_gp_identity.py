"""Synthetic operand identities and alias counterexamples; no retail bytes."""
import copy
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from RAC3_PAL.gp_identity import normalize, _GPRS
from RAC3_PAL.rac3_engine import Engine as AdapterEngine
from RAC3_PAL.tests.test_check_worker_gprel import body, memory, reference, words


NAME = 'func_L02_00001000'


def listing(raw):
    ops = {32: 'lb', 33: 'lh', 35: 'lw', 36: 'lbu', 37: 'lhu', 39: 'lwu',
           40: 'sb', 41: 'sh', 43: 'sw', 49: 'lwc1', 57: 'swc1'}
    lines = ['glabel ' + NAME]
    for offset in range(0, len(raw), 4):
        word = struct.unpack_from('<I', raw, offset)[0]
        op, rt = word >> 26, word >> 16 & 31
        if op in ops:
            register = f'$f{rt}' if op in (49, 57) else '$' + _GPRS[rt]
            displacement = (word & 0x7fff) - (word & 0x8000)
            text = f'{ops[op]} {register}, {displacement}($gp)'
        elif word == 0x03e00008:
            text = 'jr $ra'
        elif word == 0:
            text = 'nop'
        else:
            text = f'.word 0x{word:08X}'
        lines.append(f'/* {0x1000 + offset:08X} {word:08X} */  {text}')
    return '\n'.join(lines) + '\n'


class Engine:
    remnants = set()

    def __init__(self, references):
        self.references = references

    def all_places(self, name):
        return [(f'level_{number:02d}', '.text', ref['address'], len(ref['raw']))
                for number, ref in self.references.items()]

    def place(self, name):
        return self.all_places(name)[0]

    def disc_bytes(self, place):
        return self.references[int(place[0][-2:])]['raw']


class GpIdentityTests(unittest.TestCase):
    def setUp(self):
        self.refs = {2: reference(0x400), 3: reference(0x600), 4: reference(0x440)}
        self.original = listing(self.refs[2]['raw'])

    def run_rule(self, references=None, assembly=None, engine=None, checked=True, name=NAME):
        refs = references or self.refs
        original = assembly if assembly is not None else listing(refs[2]['raw'])
        with patch('RAC3_PAL.gp_identity._level_references',
                   return_value=(2, refs) if checked else None):
            return normalize(engine or Engine(refs), name, original)

    def test_only_operand_identity_changes_and_all_placements_are_recorded(self):
        output, evidence = self.run_rule()
        self.assertEqual(output, self.original.replace('1024($gp)', '%gp_rel(D_L02_00200400)($gp)'))
        self.assertEqual(evidence[0]['offsets'], [0])
        self.assertEqual([p['gpOffset'] for p in evidence[0]['placements']], [0x400, 0x600, 0x440])
        self.assertEqual([p['target'] for p in evidence[0]['placements']], [0x200400, 0x200600, 0x200440])
        self.assertTrue(all(len(p['referenceHash']) == 64 for p in evidence[0]['placements']))
        self.assertNotIn('value', evidence[0])

    def test_fixed_sda_slot_is_retained_even_when_absolute_addresses_move(self):
        refs = {2: reference(0x400), 3: reference(0x400, gp=0x210000)}
        self.assertEqual(self.run_rule(references=refs), (self.original, []))

    def test_resident_address_with_different_gps_is_not_a_moving_level_object(self):
        refs = {2: reference(0x400), 3: reference(-0x400, gp=0x200800)}
        refs[3]['data'] = refs[2]['data']
        self.assertEqual(self.run_rule(references=refs), (self.original, []))

    def test_negative_gp_offsets_and_bss_do_not_imply_an_initial_value(self):
        refs = {2: reference(-0x400), 3: reference(-0x600)}
        for ref in refs.values():
            ref['data'] = [(0x1ff000, 0x200000, True)]
        output, evidence = self.run_rule(references=refs)
        self.assertIn('%gp_rel(D_L02_001FFC00)($gp)', output)
        self.assertEqual([p['target'] for p in evidence[0]['placements']], [0x1ffc00, 0x1ffa00])

    def test_supported_widths_and_fp_gpr_register_files_remain_distinct(self):
        for op, width in ((32, 1), (33, 2), (35, 4), (36, 1), (37, 2),
                          (39, 4), (40, 1), (41, 2), (43, 4), (49, 4), (57, 4)):
            refs = {2: reference(0x400, op=op), 3: reference(0x600, op=op)}
            original = listing(refs[2]['raw'])
            output, evidence = self.run_rule(references=refs)
            self.assertEqual(output, original.replace('1024($gp)', '%gp_rel(D_L02_00200400)($gp)'))
            self.assertEqual(evidence[0]['width'], width)
        refs = {2: reference(0x400, value=28), 3: reference(0x600, value=28)}
        self.assertIn('$f28, %gp_rel(', self.run_rule(references=refs)[0])
        for op in (35, 43):
            refs = {2: reference(0x400, op=op, value=28),
                    3: reference(0x600, op=op, value=28)}
            self.assertEqual(self.run_rule(references=refs), (listing(refs[2]['raw']), []))

    def test_read_and_write_share_identity_only_with_consistent_secondary_targets(self):
        refs = copy.deepcopy(self.refs)
        for ref in refs.values():
            displacement = struct.unpack_from('<I', ref['raw'])[0] & 0xffff
            ref['raw'] = words(memory(displacement), memory(displacement, op=57), 0x03e00008, 0)
        output, evidence = self.run_rule(references=refs)
        self.assertEqual(output.count('%gp_rel(D_L02_00200400)'), 2)
        self.assertEqual(evidence[0]['offsets'], [0, 4])
        refs[3]['raw'] = words(memory(0x600), memory(0x604, op=57), 0x03e00008, 0)
        self.assertEqual(self.run_rule(references=refs), (listing(refs[2]['raw']), []))

    def test_secondary_only_overlap_rejects_separate_identities(self):
        refs = copy.deepcopy(self.refs)
        for number, ref in refs.items():
            displacement = struct.unpack_from('<I', ref['raw'])[0] & 0xffff
            other = displacement if number == 3 else displacement + 4
            ref['raw'] = words(memory(displacement), memory(other, value=13), 0x03e00008, 0)
        self.assertEqual(self.run_rule(references=refs), (listing(refs[2]['raw']), []))

    def test_mixed_width_or_signedness_or_float_identities_are_rejected(self):
        for op in (32, 35, 36):
            refs = copy.deepcopy(self.refs)
            for ref in refs.values():
                displacement = struct.unpack_from('<I', ref['raw'])[0] & 0xffff
                ref['raw'] = words(memory(displacement), memory(displacement, op=op), 0x03e00008, 0)
            self.assertEqual(self.run_rule(references=refs), (listing(refs[2]['raw']), []))

    def test_address_formation_or_unsupported_gp_memory_cannot_split_aliases(self):
        for prefix in (9 << 26 | 28 << 21 | 8 << 16 | 0x400,
                       25 << 26 | 28 << 21 | 8 << 16 | 0x400,
                       28 << 21 | 8 << 11 | 33,
                       4 << 21 | 28 << 16 | 8 << 11 | 33,
                       memory(0x400, op=55)):
            refs = copy.deepcopy(self.refs)
            for ref in refs.values():
                ref['raw'] = ref['raw'][:-8] + words(prefix, 0x03e00008, 0)
            self.assertEqual(self.run_rule(references=refs), (listing(refs[2]['raw']), []))

    def test_gp_prefix_clobbers_calls_and_branches_are_rejected(self):
        for prefix in (0x3c1c0020, 0x8c9c0000, 0x441c0000,
                       4 << 21 | 5 << 16 | 28 << 11 | 24,
                       0x0c001000, 0x10000001):
            refs = copy.deepcopy(self.refs)
            for ref in refs.values():
                ref['raw'] = words(prefix, 0) + ref['raw']
            self.assertEqual(self.run_rule(references=refs), (listing(refs[2]['raw']), []))

    def test_address_escape_in_secondary_copy_also_rejects_recovery(self):
        refs = copy.deepcopy(self.refs)
        for ref in refs.values():
            ref['raw'] = ref['raw'][:-8] + words(0, 0x03e00008, 0)
        refs[3]['raw'] = refs[3]['raw'][:4] + words(
            9 << 26 | 28 << 21 | 8 << 16 | 0x600, 0x03e00008, 0)
        self.assertEqual(self.run_rule(references=refs), (listing(refs[2]['raw']), []))

    def test_absolute_lui_aliases_in_any_copy_cannot_create_two_extern_identities(self):
        for signed_low in (False, True):
            for alias_level in (2, 3):
                refs = copy.deepcopy(self.refs)
                # Give the primary a high-bit LO so ORI and signed ADDIU
                # address formation require two different LUI high halves.
                for number, ref in refs.items():
                    ref['gp'] = 0x208000
                    ref['data'] = [(0x208000, 0x209000, True)]
                    target = ref['gp'] + (struct.unpack_from('<I', ref['raw'])[0] & 0xffff)
                    high = (target + (0x8000 if signed_low else 0)) >> 16
                    extra = (15 << 26 | 8 << 16 | high) if alias_level == number else 0
                    ref['raw'] = ref['raw'][:-8] + words(extra, 0x03e00008, 0)
                self.assertEqual(self.run_rule(references=refs), (listing(refs[2]['raw']), []))
        refs = copy.deepcopy(self.refs)
        for ref in refs.values():
            # A native -1.0f LUI is unrelated to these addresses and does not
            # establish an initializer or block the existing two real cases.
            ref['raw'] = ref['raw'][:-8] + words(0x3c08bf80, 0x03e00008, 0)
        self.assertEqual(len(self.run_rule(references=refs)[1]), 1)

    def test_zero_based_absolute_address_escape_is_rejected(self):
        refs = {2: reference(0x400, gp=0x1000), 3: reference(0x600, gp=0x1000)}
        for ref in refs.values():
            ref['data'] = [(0x1000, 0x2000, True)]
            target = ref['gp'] + (struct.unpack_from('<I', ref['raw'])[0] & 0xffff)
            ref['raw'] = ref['raw'][:-8] + words(9 << 26 | 8 << 16 | target, 0x03e00008, 0)
        self.assertEqual(self.run_rule(references=refs), (listing(refs[2]['raw']), []))

    def test_changed_register_opcode_or_data_rights_in_any_copy_reject(self):
        for change in ('register', 'opcode', 'unallocated', 'read_only_store', 'alignment'):
            refs = copy.deepcopy(self.refs)
            if change == 'register':
                refs[3]['raw'] = body(0x600, value=13)
            elif change == 'opcode':
                refs[3]['raw'] = body(0x600, op=35)
            elif change == 'unallocated':
                refs[3]['data'] = []
            elif change == 'read_only_store':
                for ref in refs.values():
                    displacement = struct.unpack_from('<I', ref['raw'])[0] & 0xffff
                    ref['raw'] = body(displacement, op=57)
                refs[3]['data'] = [(0x200000, 0x201000, False)]
            else:
                refs[3]['raw'] = body(0x601)
            self.assertEqual(self.run_rule(references=refs), (listing(refs[2]['raw']), []))

    def test_incomplete_canonical_evidence_and_listing_mismatches_fail_closed(self):
        self.assertEqual(self.run_rule(checked=False), (self.original, []))
        for modified in (self.original.replace('00001000', '00001004'),
                         self.original.replace('C78C0400', 'C78D0400'),
                         self.original.replace('1024($gp)', '1028($gp)'),
                         self.original.replace('$f12,', '$f13,'),
                         self.original.replace('lwc1', 'lw'),
                         self.original + self.original.splitlines()[1] + '\n'):
            self.assertEqual(self.run_rule(assembly=modified), (modified, []))
        engine = Engine(self.refs)
        engine.all_places = lambda name: Engine(self.refs).all_places(name)[:-1]
        self.assertEqual(self.run_rule(engine=engine), (self.original, []))
        engine.all_places = lambda name: Engine(self.refs).all_places(name) + [Engine(self.refs).place(name)]
        self.assertEqual(self.run_rule(engine=engine), (self.original, []))

    def test_missing_gp_hash_provider_or_remnant_never_recovers(self):
        refs = copy.deepcopy(self.refs)
        del refs[3]['gp']
        self.assertEqual(self.run_rule(references=refs), (self.original, []))
        with patch('RAC3_PAL.gp_identity._level_references', side_effect=ValueError('hash')):
            self.assertEqual(normalize(Engine(self.refs), NAME, self.original), (self.original, []))
        engine = Engine(self.refs)
        engine.remnants = {NAME}
        self.assertEqual(self.run_rule(engine=engine), (self.original, []))

    def test_adapter_passes_new_operand_to_m2c_without_changing_result_api(self):
        engine = Engine(self.refs)
        engine.assert_access = lambda name, owner: None
        engine.asm = lambda name: self.original
        engine.validated_gp = lambda program: self.refs[int(program[-2:])]['gp']
        engine.contracts = {}
        with tempfile.TemporaryDirectory() as directory:
            engine.root = Path(directory)
            with patch.dict('subprocess.__dict__'), \
                    patch('RAC3_PAL.gp_identity._level_references', return_value=(2, self.refs)), \
                    patch('rough.tool', return_value=Path('synthetic-m2c.py')), \
                    patch('rough.forwarding_evidence', return_value=dict(findings=[], warnings=[])), \
                    patch('subprocess.run', return_value=SimpleNamespace(
                        returncode=0, stdout='void ' + NAME + '(void) {}', stderr='')) as run:
                subprocess.__dict__.pop('CREATE_NO_WINDOW', None)
                result = AdapterEngine.decompile(engine, NAME)
            self.assertTrue(result['ok'])
            self.assertEqual(result['gpResolutions'], ['D_L02_00200400'])
            self.assertEqual(result['gpIdentities'][0]['symbol'], 'D_L02_00200400')
            supplied = (Path(result['directory']) / 'input.s').read_text(encoding='utf-8')
            self.assertIn('%gp_rel(D_L02_00200400)($gp)', supplied)
            self.assertNotIn('_sda', supplied)
            self.assertIn('--valid-syntax', run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs['creationflags'], 0)


if __name__ == '__main__':
    unittest.main()
