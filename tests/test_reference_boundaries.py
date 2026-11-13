"""Synthetic control-flow counterexamples; no game bytes or addresses."""
import hashlib
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest

from elftools.elf.elffile import ELFFile
from ee_common import complete_cfg
import reference_boundaries as boundary
from tests.elf_fixture import elf


def raw(*words):
    return struct.pack('<' + 'I' * len(words), *words)


def branch(op, rs=0, rt=0, imm=0):
    return op << 26 | rs << 21 | rt << 16 | (imm & 0xffff)


def identity(path, blob=None, sections=('.text',)):
    return dict(path=path, algorithm='sha256', digest=hashlib.sha256(blob if blob is not None else path.read_bytes()).hexdigest(),
                eeSections=list(sections), eeSectionsEvidence='Synthetic EE-only section provenance.')


class IncomingBoundaryTests(unittest.TestCase):
    def test_closed_return_tail_still_needs_external_boundary_review(self):
        # A conditional predecessor branches to a separately catalogued return.
        tail = raw(0x03e00008, 0x24020000)
        self.assertTrue(complete_cfg(tail, 0x1010))
        edges = boundary.direct_edges(raw(branch(4, rs=4, imm=3), 0, 0, 0) + tail, 0x1000)
        result = boundary.window_entries(edges, 0x1010, len(tail))
        self.assertFalse(result['eligible'])
        self.assertEqual(result['incoming'][0]['entry'], 'start')

    def test_standalone_called_zero_leaf_and_tail_jump_remain_eligible(self):
        for op, key in ((3, 'directEntryCalls'), (2, 'directEntryTailJumps')):
            edges = boundary.direct_edges(raw(op << 26 | 0x1010 >> 2, 0, 0, 0,
                                               0x03e00008, 0x24020000), 0x1000)
            result = boundary.window_entries(edges, 0x1010, 8)
            self.assertTrue(result['eligible'])
            self.assertEqual(result[key], 1)
        for word in (branch(4, imm=3), branch(4, rs=4, rt=4, imm=3),
                     branch(6, imm=3), branch(1, rt=1, imm=3)):
            result = boundary.window_entries(boundary.direct_edges(raw(word, 0), 0x1000), 0x1010, 8)
            self.assertTrue(result['eligible'])
            self.assertEqual(result['directEntryTailJumps'], 1)

    def test_internal_loop_and_exact_end_are_not_external_entries(self):
        edges = boundary.direct_edges(raw(branch(5, rs=4, rt=5, imm=-1), 0,
                                           branch(4, rs=4, imm=1), 0), 0x1000)
        self.assertTrue(boundary.window_entries(edges, 0x1000, 16)['eligible'])

    def test_external_call_and_jump_to_interior_require_review(self):
        for op in (2, 3):
            edges = boundary.direct_edges(raw(op << 26 | 0x1014 >> 2, 0), 0x1000)
            result = boundary.window_entries(edges, 0x1010, 8)
            self.assertFalse(result['eligible'])
            self.assertEqual(result['incoming'][0]['entry'], 'interior')

    def test_regimm_link_is_a_call_not_a_non_call_branch(self):
        for rt in (16, 17, 18, 19):
            edges = boundary.direct_edges(raw(branch(1, rs=4, rt=rt, imm=3), 0), 0x1000)
            self.assertEqual(edges[0]['kind'], 'call')
            self.assertTrue(boundary.window_entries(edges, 0x1010, 8)['eligible'])

    def test_branch_likely_cop_and_negative_displacements(self):
        for word in (branch(20, rs=4, imm=-5), branch(1, rs=4, rt=2, imm=-5),
                     branch(17, rs=8, rt=3, imm=-5), branch(18, rs=8, rt=0, imm=-5)):
            edge = boundary.direct_edges(raw(word, 0), 0x1020)[0]
            self.assertEqual(edge['target'], 0x1010)
            self.assertFalse(boundary.window_entries([edge], 0x1010, 8)['eligible'])

    def test_impossible_branches_and_non_branch_cop_words_are_ignored(self):
        for word in (branch(5, rs=4, rt=4, imm=3), branch(1, rt=0, imm=3),
                     branch(7, imm=3), branch(18, rs=16, imm=3)):
            self.assertEqual(boundary.direct_edges(raw(word), 0x1000), [])

    def test_jump_target_uses_pc_plus_four_high_bits(self):
        edge = boundary.direct_edges(raw(3 << 26 | 4), 0x0ffffffc)[0]
        self.assertEqual(edge['target'], 0x10000010)

    def test_all_placements_checked_and_cache_rebound_to_reference_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {p: root / (p + '.elf') for p in ('first', 'second')}
            leaf = [0, 0, 0, 0, 0x03e00008, 0x24020000]
            for path in paths.values():
                path.write_bytes(elf(leaf, executable=True))
            engine = SimpleNamespace(ELFFile=ELFFile, program_path=lambda p: paths[p],
                assert_reference=lambda p: None,
                reference_identity=lambda p: identity(paths[p[0]]),
                all_places=lambda name: [(p, '.text', 0x100010, 8) for p in paths])
            first = boundary.inspect(engine, 'synthetic')
            self.assertTrue(first['eligible'])
            old_hash = first['placements'][1]['referenceSha256']
            changed = [branch(4, rs=4, imm=3), 0, 0, 0, *leaf[4:]]
            paths['second'].write_bytes(elf(changed, executable=True))
            second = boundary.inspect(engine, 'synthetic')
            self.assertTrue(second['placements'][0]['eligible'])
            self.assertFalse(second['placements'][1]['eligible'])
            self.assertNotEqual(old_hash, second['placements'][1]['referenceSha256'])
            self.assertFalse(second['eligible'])
            self.assertTrue(second['complete'])

    def test_missing_reference_context_fails_closed(self):
        engine = SimpleNamespace(all_places=lambda name: [('main', '.text', 0x1000, 8)],
                                 assert_reference=lambda p: None)
        result = boundary.apply(engine, 'synthetic', dict(passed=True, problems=[]))
        self.assertFalse(result['passed'])
        self.assertFalse(result['boundaryChecks']['complete'])

    def test_adapter_resolves_split_executables_and_cache_separates_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = [Path(directory) / name for name in ('first.elf', 'second.elf')]
            first.write_bytes(elf([0, 0, 0, 0, 0x03e00008, 0], executable=True))
            second.write_bytes(elf([branch(4, rs=4, imm=3), 0, 0, 0, 0x03e00008, 0], executable=True))
            placement = ('main', '.text', 0x100010, 8)
            engine = SimpleNamespace(ELFFile=ELFFile, assert_reference=lambda p: None,
                program_path=lambda p: first, reference_path=lambda p: second,
                reference_identity=lambda p: identity(engine.reference_path(p)),
                all_places=lambda name: [placement])
            self.assertFalse(boundary.inspect(engine, 'synthetic')['eligible'])
            engine.reference_path = lambda p: first
            self.assertTrue(boundary.inspect(engine, 'synthetic')['eligible'])
            self.assertEqual(len(engine._incoming_boundary_cache), 2)

    def test_changed_content_after_reference_validation_is_not_certified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reference.elf'
            expected = elf([branch(4, rs=4, imm=3), 0, 0, 0, 0x03e00008, 0], executable=True)
            replacement = elf([0, 0, 0, 0, 0x03e00008, 0], executable=True)
            path.write_bytes(expected)
            def assert_then_replace(program):
                self.assertEqual(path.read_bytes(), expected)
                path.write_bytes(replacement)
            engine = SimpleNamespace(ELFFile=ELFFile, assert_reference=assert_then_replace,
                reference_identity=lambda p: identity(path, expected),
                all_places=lambda n: [('main', '.text', 0x100010, 8)])
            result = boundary.inspect(engine, 'synthetic')
            self.assertFalse(result['eligible'])
            self.assertFalse(result['complete'])
            self.assertIn('expected placement identity', result['placements'][0]['error'])

    def test_non_ee_executable_section_is_not_decoded_as_ee(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mixed.elf'
            micro = ('.micro', 1, 6, 0x100040, raw(branch(4, rs=4, imm=-13), 0), 0, 0, 0)
            path.write_bytes(elf([0, 0, 0, 0, 0x03e00008, 0], executable=True, extra_sections=[micro]))
            engine = SimpleNamespace(ELFFile=ELFFile, assert_reference=lambda p: None,
                reference_identity=lambda p: identity(path),
                all_places=lambda n: [('main', '.text', 0x100010, 8)])
            self.assertTrue(boundary.inspect(engine, 'synthetic')['eligible'])
            engine.reference_identity = lambda p: identity(path, sections=('.text', '.micro'))
            self.assertFalse(boundary.inspect(engine, 'synthetic')['eligible'])

    def test_relocatable_or_missing_architecture_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reference.elf'
            path.write_bytes(elf([0x0c000000, 0, 0, 0, 0x03e00008, 0], relocations=[(0, 4)]))
            engine = SimpleNamespace(ELFFile=ELFFile, assert_reference=lambda p: None,
                reference_identity=lambda p: identity(path),
                all_places=lambda n: [('main', '.text', 0x10, 8)])
            report = boundary.inspect(engine, 'synthetic')
            self.assertFalse(report['complete'])
            self.assertIn('ET_EXEC', report['placements'][0]['error'])
            engine.reference_identity = lambda p: dict(path=path, algorithm='sha256', digest=hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertFalse(boundary.inspect(engine, 'synthetic')['complete'])


if __name__ == '__main__':
    unittest.main()
