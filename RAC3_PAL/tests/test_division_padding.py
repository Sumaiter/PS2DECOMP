"""Native padding/configuration diagnostics with synthetic delay counterexamples."""
import copy
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from RAC3_PAL import division_padding as padding
from RAC3_PAL.rac3_engine import Engine as AdapterEngine


NAME = 'func_L02_00001000'
DIV = 0x46020803
SQRT = 0x46010004


def words(*items):
    return struct.pack('<' + 'I' * len(items), *items)


class Engine:
    def __init__(self, root, refs):
        self.root, self.refs = Path(root), refs

    def all_places(self, name):
        return [(f'level_{number:02d}', '.text', ref['address'], len(ref['raw']))
                for number, ref in self.refs.items()]

    def place(self, name):
        return self.all_places(name)[0]

    def disc_bytes(self, place):
        return self.refs[int(place[0][-2:])]['raw']


class DivisionPaddingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.table = self.root / padding.TABLE
        self.table.parent.mkdir(parents=True)
        self.write_table('# table\n')
        raw = words(0, 0, DIV, 0x03e00008, 0)
        self.refs = {2: dict(address=0x1000, raw=raw), 3: dict(address=0x2000, raw=raw)}

    def write_table(self, text):
        self.table.write_text(text, encoding='utf-8', newline='\n')

    def inspect(self, refs=None, engine=None, checked=True, name=NAME):
        refs = refs if refs is not None else self.refs
        with patch.object(padding, '_level_references', return_value=(2, refs) if checked else None):
            return padding.inspect(engine or Engine(self.root, refs), name)

    def test_missing_entry_records_every_reference_without_mutating_inputs(self):
        before = self.table.read_bytes()
        result = self.inspect()
        self.assertEqual(result['status'], 'missing-entry')
        self.assertEqual(result['nativeCounts'], [2])
        self.assertIsNone(result['configuredCounts'])
        self.assertEqual(len(result['placements']), 2)
        self.assertEqual(result['placements'][0]['sites'][0]['nopOffsets'], [0, 4])
        self.assertTrue(all(len(row['referenceSha256']) == 64 for row in result['placements']))
        self.assertFalse(result['mutatesInputs'])
        self.assertFalse(result['changesSearchPolicy'])
        self.assertFalse(result['semanticApproval'])
        self.assertEqual(self.table.read_bytes(), before)

    def test_normal_conditional_likely_and_call_delay_nops_are_excluded(self):
        for control in (0x10000001, 0x14400001, 0x54400001, 0x04400001,
                        0x45000001, 0x45030001, 0x49000001,
                        0x0c001000, 0x0100f809, 0x03e00008):
            sites = padding.native_sites(words(control, 0, DIV, 0x03e00008, 0))
            self.assertEqual(sites[0]['count'], 0)
            self.assertEqual(sites[0]['excludedDelayNopOffset'], 4)
        sites = padding.native_sites(words(0x14400002, 0, 0, DIV, 0x03e00008, 0))
        self.assertEqual(sites[0]['count'], 1)
        self.assertEqual(sites[0]['nopOffsets'], [8])

    def test_division_and_sqrt_counts_keep_native_order_and_reserved_sqrt_is_rejected(self):
        sites = padding.native_sites(words(0, DIV, 0, 0, SQRT, 0x03e00008, 0))
        self.assertEqual([(row['mnemonic'], row['count']) for row in sites], [('div.s', 1), ('sqrt.s', 2)])
        self.assertIsNone(padding.native_sites(words(SQRT | 1 << 11)))
        self.assertIsNone(padding.native_sites(b'\x00'))
        self.assertIsNone(padding.native_sites(b''))

    def test_configured_counts_different_count_and_different_operation_number_are_distinct(self):
        for row, expected in ((NAME + ' 2\n', 'configured-corroborated'),
                              (NAME + ' 1\n', 'padding-count-mismatch'),
                              (NAME + ' 2 0\n', 'operation-count-mismatch')):
            self.write_table(row)
            result = self.inspect()
            self.assertEqual(result['status'], expected)
            self.assertEqual(result['nativeCounts'], [2])

    def test_zero_padding_and_no_divider_do_not_report_missing_configuration(self):
        for raw, status in ((words(DIV, 0x03e00008, 0), 'no-padding-required'),
                            (words(0x03e00008, 0), 'not-applicable')):
            refs = {2: dict(address=0x1000, raw=raw), 3: dict(address=0x2000, raw=raw)}
            self.assertEqual(self.inspect(refs=refs)['status'], status)
        self.assertEqual(self.inspect(name='func_00001000')['status'], 'not-applicable')
        raw = words(0x03e00008, 0)
        refs = {2: dict(address=0x1000, raw=raw), 3: dict(address=0x2000, raw=raw)}
        with patch.object(padding, '_level_references') as producer:
            result = padding.inspect(Engine(self.root, refs), NAME)
        producer.assert_not_called()
        self.assertEqual(result['scope'], 'no-primary-operation-and-no-config-entry')
        self.assertEqual(result['placements'], [])

    def test_different_native_copies_are_ambiguous_not_a_primary_only_recommendation(self):
        refs = copy.deepcopy(self.refs)
        refs[3]['raw'] = words(0x24020000, 0, DIV, 0x03e00008, 0)
        result = self.inspect(refs=refs)
        self.assertEqual(result['status'], 'inconsistent-native-padding')
        self.assertIsNone(result['nativeCounts'])
        self.assertEqual(len(result['placements']), 2)

    def test_malformed_duplicate_or_missing_table_never_silently_supplies_counts(self):
        for text in (NAME + '\n', NAME + ' -1\n', NAME + ' 1.0\n', NAME + ' 0x2\n',
                     NAME + ' 2\n' + NAME + ' 1\n', 'arbitrary 2\n'):
            self.write_table(text)
            self.assertEqual(self.inspect()['status'], 'invalid-table')
        self.table.unlink()
        self.assertEqual(self.inspect()['status'], 'missing-table')

    def test_missing_hash_checked_references_and_changed_canonical_placements_remain_unavailable(self):
        self.assertEqual(self.inspect(checked=False)['status'], 'unavailable-native-evidence')
        engine = Engine(self.root, self.refs)
        engine.all_places = lambda name: [(f'level_02', '.text', 0x1000, 20)]
        self.assertEqual(self.inspect(engine=engine)['status'], 'unavailable-native-evidence')
        engine = Engine(self.root, self.refs)
        engine.all_places = lambda name: Engine(self.root, self.refs).all_places(name) * 2
        self.assertEqual(self.inspect(engine=engine)['status'], 'unavailable-native-evidence')
        with patch.object(padding, '_level_references', side_effect=ValueError('hash changed')):
            self.assertEqual(padding.inspect(Engine(self.root, self.refs), NAME)['status'], 'unavailable-native-evidence')

    def test_warning_has_english_and_german_coverage_and_does_not_approve_configuration(self):
        result = self.inspect()
        self.assertIn('Diagnostic only', padding.warnings(result, 'en')[0])
        self.assertIn('Nur Diagnose', padding.warnings(result, 'de-DE')[0])
        result['status'] = 'configured-corroborated'
        self.assertEqual(padding.warnings(result, 'en'), [])

    def test_decompile_adds_evidence_and_warning_without_changing_m2c_input_or_timeout(self):
        engine = Engine(self.root, self.refs)
        engine.assert_access = lambda name, owner: None
        engine.asm = lambda name: 'glabel ' + name + '\n/* 00001000 46020803 */  div.s $f0, $f1, $f2\n'
        engine.contracts = {}
        evidence = self.inspect()
        original_flag = getattr(subprocess, 'CREATE_NO_WINDOW', None)
        if hasattr(subprocess, 'CREATE_NO_WINDOW'):
            del subprocess.CREATE_NO_WINDOW
        self.addCleanup(lambda: setattr(subprocess, 'CREATE_NO_WINDOW', original_flag)
                        if original_flag is not None else None)
        with patch.object(padding, 'inspect', return_value=evidence), \
                patch('rough.tool', return_value=Path('synthetic-m2c.py')), \
                patch('rough.forwarding_evidence', return_value=dict(findings=[], warnings=[])), \
                patch('local_process.run', return_value=SimpleNamespace(
                    returncode=0, stdout='void ' + NAME + '(void) {}', stderr='')) as run, \
                patch.dict(os.environ, PS2DECOMP_LANGUAGE='en'):
            result = AdapterEngine.decompile(engine, NAME)
        self.assertEqual(result['divisionPadding'], evidence)
        self.assertTrue(any('missing-entry' in warning for warning in result['warnings']))
        self.assertEqual((Path(result['directory']) / 'input.s').read_text(), engine.asm(NAME))
        self.assertEqual(run.call_args.kwargs['timeout'], 45)
        self.assertNotIn('creationflags', run.call_args.kwargs)
        self.assertEqual(self.table.read_text(), '# table\n')


if __name__ == '__main__':
    unittest.main()
