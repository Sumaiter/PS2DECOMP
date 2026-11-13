"""Synthetic relocation counterexamples; no game bytes or proprietary tools."""
import copy
import struct
import unittest

from RAC3_PAL.check_worker import _base_unchanged, _named_level_targets


NAME = 'D_L02_00200000'


def words(*items):
    return struct.pack('<' + 'I' * len(items), *items)


def pair(address, low_op=35, base=8, value=2):
    low = address & 0xffff
    high = (address + (0x10000 if low & 0x8000 else 0)) >> 16
    return words(15 << 26 | base << 16 | high,
                 low_op << 26 | base << 21 | value << 16 | low,
                 0x03e00008, 0)


def reference(address, base=0x200000, **kwargs):
    return dict(address=0x1000, raw=pair(address, **kwargs), gp=0x180000,
                data=[(base, base + 0x1000, True)])


def synthetic_derive(data, ref, relocations, address, gp):
    """Callback fixture for the existing project derive protocol.

    Production injects tools.level_symbols.derive unchanged. This fixture
    checks the caller's HI/LO grouping and the supported REL addend equations.
    """
    if len(data) != len(ref):
        raise ValueError('length')
    out = {}
    hi, *lows = relocations
    if hi[0:2] != (0, 5) or not lows:
        raise ValueError('group')
    mine_hi, actual_hi = struct.unpack_from('<I', data, 0)[0], struct.unpack_from('<I', ref, 0)[0]
    for offset, kind, symbol in lows:
        if kind != 6 or symbol != hi[2]:
            raise ValueError('group member')
        mine = struct.unpack_from('<I', data, offset)[0]
        actual = struct.unpack_from('<I', ref, offset)[0]
        if (mine_hi ^ actual_hi) & 0xffff0000 or (mine ^ actual) & 0xffff0000:
            raise ValueError('instruction')
        low = lambda n: (n & 0x7fff) - (n & 0x8000)
        target = (((actual_hi & 0xffff) << 16) + low(actual)
                  - ((mine_hi & 0xffff) << 16) - low(mine)) & 0xffffffff
        if out.setdefault(symbol, target) != target:
            raise ValueError('contradictory target')
    return out


class NamedLevelTargetTests(unittest.TestCase):
    def setUp(self):
        self.body = pair(0)
        self.relocations = [(0, 5, NAME), (4, 6, NAME)]
        self.references = {2: reference(0x200000), 3: reference(0x210000, base=0x210000)}

    def run_rule(self, body=None, relocations=None, references=None, primary=2,
                 derive=synthetic_derive, blocked_symbols=()):
        return _named_level_targets(self.body if body is None else body,
                                    self.relocations if relocations is None else relocations,
                                    self.references if references is None else references,
                                    primary, derive, blocked_symbols)

    def assert_rejected(self, **kwargs):
        self.assertEqual(self.run_rule(**kwargs), ({}, []))

    def test_primary_anchor_and_every_actual_target_are_retained(self):
        mapped, evidence = self.run_rule()
        self.assertEqual(mapped, {NAME: {2: 0x200000, 3: 0x210000}})
        self.assertEqual(evidence[0]['policy'], 'tools.level_symbols.derive')
        self.assertEqual([row['offsets'] for row in evidence[0]['placements']], [[0, 4], [0, 4]])
        self.assertEqual([row['uses'][0]['address'] for row in evidence[0]['placements']], [0x200000, 0x210000])

    def test_existing_rel_addend_is_preserved(self):
        body = pair(4)
        references = {2: reference(0x200004), 3: reference(0x210004, base=0x210000)}
        self.assertEqual(self.run_rule(body=body, references=references)[0], {NAME: {2: 0x200000, 3: 0x210000}})

    def test_signed_low_and_high_carry_are_preserved(self):
        name = 'D_L02_0020FFF0'
        references = {2: reference(0x20fff0, base=0x20f000), 3: reference(0x21fff0, base=0x21f000)}
        relocs = [(0, 5, name), (4, 6, name)]
        self.assertEqual(self.run_rule(relocations=relocs, references=references)[0],
                         {name: {2: 0x20fff0, 3: 0x21fff0}})

    def test_different_near_miss_bytes_are_never_substituted(self):
        body = self.body + words(0x24020001)
        references = {key: dict(value, raw=value['raw'] + words(0x24020002)) for key, value in self.references.items()}
        mapped, _ = self.run_rule(body=body, references=references)
        self.assertTrue(mapped)
        self.assertNotEqual(body[-4:], references[2]['raw'][-4:])
        self.assertEqual(body[-4:], words(0x24020001))

    def test_wrong_anchor_address_is_not_learned_from_another_level(self):
        self.references[2]['raw'] = pair(0x200004)
        self.assert_rejected()

    def test_anchor_level_must_be_present(self):
        self.assert_rejected(references={3: self.references[3]})
        self.assert_rejected(primary=3)

    def test_missing_or_invalid_gp_fails_closed(self):
        for value in (None, 0, -1, True, 1 << 32):
            with self.subTest(value=value):
                references = copy.deepcopy(self.references)
                references[3]['gp'] = value
                self.assert_rejected(references=references)

    def test_wrong_register_opcode_and_offsets_fail_closed(self):
        for raw in (pair(0x210000, base=9), pair(0x210000, value=3),
                    pair(0x210000, low_op=49), words(0x44821000, 0x46000002, 0x03e00008, 0),
                    words(0, *struct.unpack('<4I', pair(0x210000)))):
            with self.subTest(raw=raw.hex()):
                references = copy.deepcopy(self.references)
                references[3]['raw'] = raw
                self.assert_rejected(references=references)
        self.assert_rejected(relocations=[(0, 5, NAME), (8, 6, NAME)])

    def test_short_and_unaligned_relocations_fail_closed(self):
        self.assert_rejected(body=self.body[:-1])
        self.assert_rejected(relocations=[(0, 5, NAME), (3, 6, NAME)])
        self.assert_rejected(relocations=[(0, 5, NAME), (100, 6, NAME)])
        self.references[3]['raw'] = self.references[3]['raw'][:4]
        self.assert_rejected()

    def test_unsupported_format_and_duplicate_offsets_stay_unresolved(self):
        for relocs in ([(0, 5, NAME), (4, 8, NAME)], [(0, 5, NAME), (4, 2, NAME)],
                       [(0, 5, NAME), (4, 6, NAME), (4, 8, '')], [(4, 6, NAME)]):
            with self.subTest(relocs=relocs):
                self.assert_rejected(relocations=relocs)

    def test_nonlevel_or_foreign_anchor_names_are_not_guessed(self):
        for symbol in ('arbitrary_global', 'D_00200000', 'D_L01_00200000', 'D_L02_00200000$bad'):
            with self.subTest(symbol=symbol):
                self.assert_rejected(relocations=[(0, 5, symbol), (4, 6, symbol)])

    def test_symbol_defined_in_own_stdata_or_text_is_never_retargeted(self):
        self.assert_rejected(blocked_symbols={NAME})

    def test_data_bounds_and_write_rights_are_required_at_every_placement(self):
        for data in ([], [(0x210001, 0x211000, True)], [(0x210000, 0x210003, True)]):
            with self.subTest(data=data):
                references = copy.deepcopy(self.references)
                references[3]['data'] = data
                self.assert_rejected(references=references)
        body = pair(0, low_op=43)
        references = {2: reference(0x200000, low_op=43), 3: reference(0x210000, base=0x210000, low_op=43)}
        references[3]['data'] = [(0x210000, 0x211000, False)]
        self.assert_rejected(body=body, references=references)

    def test_native_access_alignment_and_address_wrap_are_not_accepted(self):
        for address in (0x210001, 0xfffffffe):
            with self.subTest(address=hex(address)):
                references = copy.deepcopy(self.references)
                references[3] = reference(address, base=address)
                self.assert_rejected(references=references)

    def test_repeated_low_targets_must_agree(self):
        # An FP destination keeps the LUI's GPR available for both reads.
        body = words(0x3c080000, 0xc5020000, 0xc5030000, 0x03e00008, 0)
        refs = {2: dict(self.references[2], raw=words(0x3c080020, 0xc5020000, 0xc5030000, 0x03e00008, 0)),
                3: dict(self.references[3], raw=words(0x3c080021, 0xc5020000, 0xc5030004, 0x03e00008, 0))}
        self.assert_rejected(body=body, relocations=[(0, 5, NAME), (4, 6, NAME), (8, 6, NAME)], references=refs)

    def test_r5900_mult_destination_and_call_invalidate_base(self):
        multiply = 4 << 21 | 5 << 16 | 8 << 11 | 24
        for middle in (multiply, multiply | 1, 0x0c001000, 0x10000001, 0x46000000 | 8 << 11):
            with self.subTest(middle=hex(middle)):
                data = words(0x3c080000, middle, 0x8d020000, 0x03e00008, 0)
                # Ordinary FP arithmetic writes no GPR; all other forms above
                # either clobber t0 or create a control edge.
                if middle >> 26 == 17:
                    self.assertTrue(_base_unchanged(data, 0, 8, 8))
                else:
                    self.assertFalse(_base_unchanged(data, 0, 8, 8))

    def test_project_policy_exception_stays_unresolved(self):
        def fail(*args):
            raise RuntimeError('project derive rejected symbol')
        self.assert_rejected(derive=fail)


if __name__ == '__main__':
    unittest.main()
