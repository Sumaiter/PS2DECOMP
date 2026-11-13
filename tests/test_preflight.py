import re
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import candidate_preflight


def i(op, rs=0, rt=0, imm=0):
    return op << 26 | rs << 21 | rt << 16 | (imm & 0xffff)


def r(fn, rs=0, rt=0, rd=0):
    return rs << 21 | rt << 16 | rd << 11 | fn


def raw(*words):
    return struct.pack('<' + 'I' * len(words), *words)


JR = r(8, rs=31)


class CandidatePreflightTests(unittest.TestCase):
    def check_code(self, words, expected, code=None, start=0x1000):
        result = candidate_preflight.inspect(raw(*words), start)
        self.assertEqual(expected, result['eligible'], result)
        if code:
            self.assertIn(code, {row['code'] for row in result['evidence']}, result)
        return result

    def test_leaf_and_padding(self):
        self.check_code([JR, i(9, rt=2, imm=1), 0, 0], True)

    def test_restore_in_return_delay(self):
        self.check_code([i(9, rs=29, rt=29, imm=-16), JR,
                         i(9, rs=29, rt=29, imm=16)], True)

    def test_daddiu_frame(self):
        self.check_code([i(25, rs=29, rt=29, imm=-32), JR,
                         i(25, rs=29, rt=29, imm=32)], True)

    def test_call_delay_and_saved_register_frame(self):
        self.check_code([i(9, rs=29, rt=29, imm=-16),
                         i(63, rs=29, rt=31, imm=8),
                         3 << 26 | 0x1800 >> 2, 0,
                         i(55, rs=29, rt=31, imm=8),
                         JR, i(9, rs=29, rt=29, imm=16)], True,
                        'eabi_call_assumption')

    def test_indirect_eabi_call(self):
        self.check_code([r(9, rs=25, rd=31), 0, JR, 0], True)

    def test_call_delay_allocation_is_checked(self):
        self.check_code([3 << 26 | 0x1800 >> 2,
                         i(9, rs=29, rt=29, imm=-16), JR,
                         i(9, rs=29, rt=29, imm=16)], True)

    def test_multiple_independent_epilogues(self):
        self.check_code([i(9, rs=29, rt=29, imm=-16),
                         i(4, rs=4, imm=3), 0,
                         JR, i(9, rs=29, rt=29, imm=16),
                         i(9, rs=29, rt=29, imm=16), JR, 0], True)

    def test_all_exit_paths_must_balance(self):
        self.check_code([i(9, rs=29, rt=29, imm=-16),
                         i(4, rs=4, imm=3), 0,
                         JR, i(9, rs=29, rt=29, imm=16),
                         i(9, rs=29, rt=29, imm=8), JR, 0], False,
                        'unbalanced_return')

    def test_likely_taken_delay_only(self):
        # Taken edge allocates in the delay slot; not-taken skips the slot
        # and allocates at +8. Both reach the common balanced epilogue.
        self.check_code([i(20, rs=4, imm=2),
                         i(9, rs=29, rt=29, imm=-16),
                         i(9, rs=29, rt=29, imm=-16),
                         JR, i(9, rs=29, rt=29, imm=16)], True)

    def test_normal_branch_executes_delay_on_both_paths(self):
        self.check_code([i(4, rs=4, imm=2),
                         i(9, rs=29, rt=29, imm=-16),
                         i(9, rs=29, rt=29, imm=-16),
                         JR, i(9, rs=29, rt=29, imm=16)], False,
                        'unbalanced_return')

    def test_statically_false_likely_annuls_delay(self):
        self.check_code([i(21, imm=2),
                         i(9, rs=29, rt=29, imm=-16), JR, 0], True)

    def test_normal_statically_false_branch_keeps_delay(self):
        self.check_code([i(5, imm=2),
                         i(9, rs=29, rt=29, imm=-16), JR, 0], False,
                        'unbalanced_return')

    def test_fp_branch_likely_annuls_stack_change(self):
        self.check_code([17 << 26 | 8 << 21 | 3 << 16 | 2,
                         i(9, rs=29, rt=29, imm=-16),
                         i(9, rs=29, rt=29, imm=-16),
                         JR, i(9, rs=29, rt=29, imm=16)], True)

    def test_known_frame_pointer_restoration(self):
        self.check_code([i(9, rs=29, rt=29, imm=-32),
                         r(45, rs=29, rd=30),
                         i(9, rs=29, rt=29, imm=-16),
                         r(45, rs=30, rd=29), JR,
                         i(9, rs=29, rt=29, imm=32)], True)

    def test_unknown_frame_pointer_not_invented(self):
        self.check_code([r(45, rs=30, rd=29), JR, 0], False,
                        'unknown_sp_write')

    def test_unknown_loaded_sp_not_invented(self):
        self.check_code([i(35, rs=4, rt=29), JR, 0], False,
                        'unknown_sp_write')

    def test_dynamic_stack_change_not_invented(self):
        self.check_code([r(35, rs=29, rt=4, rd=29), JR, 0], False,
                        'unknown_sp_write')

    def test_positive_stack_without_prologue(self):
        self.check_code([i(9, rs=29, rt=29, imm=64), JR, 0], False,
                        'stack_above_entry')

    def test_trapping_arithmetic_is_explicitly_unsupported(self):
        for word in [r(32, rs=4, rt=5, rd=2), r(34, rs=4, rt=5, rd=2),
                     r(44, rs=4, rt=5, rd=2), r(46, rs=4, rt=5, rd=2),
                     i(8, rs=4, rt=2, imm=1), i(24, rs=4, rt=2, imm=1)]:
            with self.subTest(word=hex(word)):
                self.check_code([word, JR, 0], False, 'trapping_arithmetic')

    def test_unsigned_arithmetic_is_supported(self):
        for word in [r(33, rs=4, rt=5, rd=2), r(35, rs=4, rt=5, rd=2),
                     r(45, rs=4, rt=5, rd=2), i(9, rs=4, rt=2, imm=1)]:
            with self.subTest(word=hex(word)):
                self.check_code([word, JR, 0], True)

    def test_fpu_register_29_is_not_the_stack_pointer(self):
        self.check_code([i(49, rs=4, rt=29), i(57, rs=4, rt=29),
                         17 << 26 | 4 << 21 | 29 << 16,
                         JR, 0], True)

    def test_internal_direct_jump(self):
        self.check_code([2 << 26 | 0x1008 >> 2, 0, JR, 0], True)

    def test_external_conditional_edge_is_rejected(self):
        self.check_code([i(4, rs=4, imm=20), 0, JR, 0], False,
                        'outside_window')

    def test_known_identity_sp_copy(self):
        self.check_code([r(45, rs=29, rd=29), JR, 0], True)

    def test_cop2_and_vu_memory_operations_rejected(self):
        for word in [0x4A0003BF, 0x48421800, 0x48C21800,
                     i(54, rs=4, rt=15), i(62, rs=4, rt=15)]:
            with self.subTest(word=hex(word)):
                self.check_code([word, JR, 0], False, 'vu_cop2')

    def test_tail_call_and_indirect_jump_rejected(self):
        self.check_code([2 << 26 | 0x2000 >> 2, 0], False, 'tail_call')
        self.check_code([r(8, rs=25), 0], False, 'indirect_jump')

    def test_invalid_or_control_delay_slot_rejected(self):
        self.check_code([JR], False, 'missing_delay')
        self.check_code([JR, JR], False, 'control_in_delay')

    def test_nonzero_unreachable_fragment_rejected(self):
        self.check_code([JR, 0, i(9, rt=2, imm=1)], False, 'unreachable_code')

    def test_balanced_loop_is_bounded(self):
        self.check_code([i(9, rs=29, rt=29, imm=-16),
                         i(5, rs=4, imm=-1), 0,
                         JR, i(9, rs=29, rt=29, imm=16)], True)

    def test_unbalanced_stack_loop_is_bounded(self):
        self.check_code([i(9, rs=29, rt=29, imm=-16),
                         i(4, imm=-2), 0], False, 'stack_cycle')

    def test_invalid_input_and_window_limits(self):
        self.assertFalse(candidate_preflight.inspect(b'', 0x1000)['eligible'])
        self.assertFalse(candidate_preflight.inspect(b'abc', 0x1000)['eligible'])
        self.assertFalse(candidate_preflight.inspect(raw(JR, 0), 0x1001)['eligible'])
        self.assertFalse(candidate_preflight.inspect(raw(JR, 0), -4)['eligible'])
        self.assertFalse(candidate_preflight.inspect(raw(JR, 0), 'bad')['eligible'])
        self.assertFalse(candidate_preflight.inspect(b'\0' * 4 * (candidate_preflight.MAX_WORDS + 1), 0)['eligible'])

    def test_retail_malformed_window_verified_against_actual_bytes(self):
        repo = Path(__file__).parent / 'fixtures' / 'project'
        original = repo / 'build/review_auto01/b/func_00528650/original.s'
        if not original.exists():
            self.skipTest('Local reviewed retail fixture is unavailable.')
        # This read-only fixture check is independent of later authorized
        # review-owner transfers. It neither compiles nor changes the function.
        fixture = raw(*(int(h, 16) for h in re.findall(r'/\* [0-9A-F]+ ([0-9A-F]{8}) \*/', original.read_text())))
        from auto_engine import AutoEngine
        engine = AutoEngine(repo)
        actual = engine.check.disc_bytes(engine.place('func_00528650')[:4])
        self.assertEqual(actual, fixture, 'Saved disassembly must equal the actual reference bytes.')
        result = candidate_preflight.inspect(actual, engine.place('func_00528650')[2])
        self.assertFalse(result['eligible'])
        self.assertIn('stack_above_entry', {x['code'] for x in result['evidence']})

    @unittest.skip("Original game/compiler review evidence stays in the originating local repository.")
    def test_reviewed_real_valid_windows_are_not_false_positives(self):
        base = Path(__file__).parent / 'fixtures' / 'project'
        if not base.is_dir():
            self.skipTest('Local reviewed retail fixtures are unavailable.')
        for name in ['func_0054BD48', 'func_00454410', 'func_01EB4E68', 'func_01EF2D88']:
            with self.subTest(name=name):
                original = base / name / 'original.s'
                fixture = raw(*(int(h, 16) for h in re.findall(r'/\* [0-9A-F]+ ([0-9A-F]{8}) \*/', original.read_text())))
                result = candidate_preflight.inspect(fixture, int(name[-8:], 16))
                self.assertTrue(result['eligible'], result)


if __name__ == '__main__':
    unittest.main()
