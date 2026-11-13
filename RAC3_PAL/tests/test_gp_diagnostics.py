"""Static operand variation must never widen identity recovery or ABI gates."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from RAC3_PAL import gp_diagnostics as diagnostics, gp_identity
from RAC3_PAL.rac3_engine import Engine as AdapterEngine
from RAC3_PAL.tests.test_gp_identity import Engine, NAME, listing
from RAC3_PAL.tests.test_check_worker_gprel import memory, reference, words


class GpDiagnosticsTests(unittest.TestCase):
    def run_rule(self, refs, text=None, checked=True):
        original = text if text is not None else listing(refs[2]['raw'])
        with patch.object(gp_identity, '_level_references', return_value=(2, refs) if checked else None):
            previous = gp_identity.normalize(Engine(refs), NAME, original)
            normalized, identities, evidence = gp_identity.normalize_with_diagnostics(Engine(refs), NAME, original)
        self.assertEqual((normalized, identities), previous)
        return normalized, identities, evidence

    def blocked(self, prefix=0x10400001):
        refs = {2: reference(0x400), 3: reference(0x600)}
        for number, ref in refs.items():
            ref['raw'] = words(prefix, 0, memory(0x400 if number == 2 else 0x600), 0x03e00008, 0)
        return refs

    def test_branch_and_call_prefixes_report_native_variation_without_recovery(self):
        for prefix in (0x10400001, 0x0c001000):
            refs = self.blocked(prefix)
            normalized, identities, evidence = self.run_rule(refs)
            self.assertEqual(normalized, listing(refs[2]['raw']))
            self.assertEqual(identities, [])
            finding, = evidence['findings']
            self.assertEqual(finding['offsets'], [8])
            self.assertEqual(finding['reason'], 'unproven-entry-gp-prefix')
            self.assertFalse(finding['entryGpPrefixVerified'])
            self.assertEqual([p['uses'][0]['gpOffset'] for p in finding['placements']], [0x400, 0x600])
            self.assertFalse(evidence['mutatesInputs'])
            self.assertFalse(evidence['semanticApproval'])

    def test_delay_slot_use_keeps_existing_conservative_call_rejection(self):
        refs = self.blocked()
        for number, ref in refs.items():
            ref['raw'] = words(0x0c001000, memory(0x400 if number == 2 else 0x600), 0x03e00008, 0)
        _, identities, evidence = self.run_rule(refs)
        self.assertEqual(identities, [])
        self.assertEqual(evidence['findings'][0]['offsets'], [4])
        self.assertFalse(evidence['findings'][0]['entryGpPrefixVerified'])

    def test_fixed_sda_slots_and_successfully_recovered_objects_do_not_warn(self):
        for refs in ({2: reference(0x400), 3: reference(0x400, gp=0x210000)},
                     {2: reference(0x400), 3: reference(0x600)}):
            _, _, evidence = self.run_rule(refs)
            self.assertEqual(evidence['findings'], [])
            self.assertEqual(diagnostics.warnings(evidence), [])

    def test_resident_target_via_different_gps_is_only_an_operand_observation(self):
        refs = {2: reference(0x400), 3: reference(-0x400, gp=0x200800)}
        refs[3]['data'] = refs[2]['data']
        _, identities, evidence = self.run_rule(refs)
        self.assertEqual(identities, [])
        finding, = evidence['findings']
        self.assertEqual(finding['reason'], 'identity-not-recovered')
        self.assertNotIn('symbol', finding)
        self.assertNotIn('target', finding)
        self.assertNotIn('type', finding)

    def test_changed_opcode_register_listing_or_missing_reference_suppresses_observation(self):
        refs = self.blocked()
        refs[3]['raw'] = words(0x10400001, 0, memory(0x600, value=13), 0x03e00008, 0)
        self.assertEqual(self.run_rule(refs)[2]['findings'], [])
        refs = self.blocked()
        for text in (listing(refs[2]['raw']).replace('00001000', '00001004'),
                     listing(refs[2]['raw']) + listing(refs[2]['raw']).splitlines()[1] + '\n'):
            evidence = self.run_rule(refs, text)[2]
            self.assertEqual(evidence['status'], 'unavailable')
            self.assertEqual(evidence['findings'], [])
        self.assertEqual(self.run_rule(refs, checked=False)[2]['status'], 'unavailable')

    def test_diagnostic_failure_does_not_change_existing_successful_recovery(self):
        refs = {2: reference(0x400), 3: reference(0x600)}
        with patch.object(diagnostics, 'observe', side_effect=ValueError('Synthetic unavailable observation')):
            normalized, identities, evidence = self.run_rule(refs)
        self.assertTrue(identities)
        self.assertIn('%gp_rel(D_L02_00200400)', normalized)
        self.assertEqual(evidence['status'], 'unavailable')
        self.assertEqual(evidence['findings'], [])

    def test_english_and_german_warnings_limit_the_observation(self):
        evidence = self.run_rule(self.blocked())[2]
        self.assertIn('fallback SDA names do not prove object identity', diagnostics.warnings(evidence, 'en')[0])
        self.assertIn('beweisen keine Objektidentität', diagnostics.warnings(evidence, 'de-DE')[0])

    def test_decompile_records_diagnostic_with_unchanged_fallback_source_and_abi_gate(self):
        refs = self.blocked()
        engine = Engine(refs)
        engine.remnants, engine.contracts = set(), {}
        engine.assert_access = lambda name, owner: None
        engine.asm = lambda name: listing(refs[2]['raw'])
        engine.validated_gp = lambda program: 0x200000
        output = 'void ' + NAME + '(void) {}\n'
        with tempfile.TemporaryDirectory() as folder:
            engine.root = Path(folder)
            with patch.object(gp_identity, '_level_references', return_value=(2, refs)), \
                    patch('rough.tool', return_value=Path('synthetic-m2c.py')), \
                    patch('rough.forwarding_evidence', return_value=dict(findings=[], warnings=[])), \
                    patch('RAC3_PAL.division_padding.inspect', return_value=dict(status='not-applicable')), \
                    patch.dict('sys.modules', gp=SimpleNamespace(STORY_GP=0x200000)), \
                    patch('local_process.run', return_value=SimpleNamespace(returncode=0, stdout=output, stderr='')) as run:
                result = AdapterEngine.decompile(engine, NAME)
            self.assertEqual(result['gpIdentities'], [])
            self.assertEqual(result['gpResolutions'], ['D_00200400_sda'])
            self.assertEqual(len(result['gpIdentityDiagnostics']['findings']), 1)
            self.assertIn('%gp_rel(D_00200400_sda)($gp)', Path(result['directory'], 'input.s').read_text())
            self.assertEqual(Path(result['directory'], 'original.s').read_text(), engine.asm(NAME))
            self.assertEqual(result['source'], '#include "common.h"\n\n/* Unverified automatic m2c draft; review types, ABI and ownership. */\n\n' + output)
            self.assertEqual(result['abiProblems'], ['Nicht dekodierte Originalinstruktion bei 00001000'])
            self.assertEqual(run.call_args.kwargs['timeout'], 45)


if __name__ == '__main__':
    unittest.main()
