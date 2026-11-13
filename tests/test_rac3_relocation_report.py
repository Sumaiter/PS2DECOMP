"""Resolved relocation targets still need to agree with the native placement."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

from RAC3_PAL.rac3_engine import Engine


class RelocationReportTests(unittest.TestCase):
    def compare(self, ours, native, records, unresolved=()):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        engine = object.__new__(Engine)
        engine.root = Path(temporary.name)
        engine.assert_access = lambda name, owner: None
        engine.place = lambda name: ('main', '.text', 0x100000, len(native) * 4, 'function')

        def probe(name, source, place, folder, timeout):
            folder.mkdir(parents=True)
            (folder / 'source.bin').write_bytes(struct.pack('<' + 'I' * len(ours), *ours))
            (folder / 'target.bin').write_bytes(struct.pack('<' + 'I' * len(native), *native))
            return dict(ok=True, technicalExact=ours == native and not unresolved,
                        compiledSize=len(ours) * 4, flags=[], relocations=records,
                        coveredRelocations=[[r['offset'], r['type']] for r in records],
                        unresolvedRelocations=list(unresolved), dataSections=[], extraDefinitions=[])

        engine._compare_place = probe
        result = engine.compare('func_00100000', '/* synthetic report fixture */\n')
        persisted = json.loads(Path(result['snapshot']).with_name('result.json').read_text(encoding='utf-8'))
        self.assertEqual(persisted, result)
        if not result['byteMatch']:
            self.assertFalse(engine.strict('func_00100000', result)['passed'])
        return result

    def test_resolved_call_to_wrong_target_is_not_a_pass(self):
        # Both JAL targets resolve, but the synthetic native call is different.
        record = dict(offset=0, type=4, symbol='synthetic_call', ok=False)
        result = self.compare([0x0c040800, 0], [0x0c040400, 0], [record])
        self.assertFalse(result['relocations']['passed'])
        self.assertEqual(result['relocations']['checked'], [record])
        self.assertEqual(result['relocations']['unresolved'], [])

    def test_matching_targets_can_pass_while_unrelated_instructions_still_differ(self):
        record = dict(offset=0, type=4, symbol='synthetic_call', ok=True)
        result = self.compare([0x0c040400, 0x2402002a], [0x0c040400, 0x2402002b], [record])
        self.assertTrue(result['relocations']['passed'])
        self.assertFalse(result['byteMatch'])

    def test_one_bad_or_missing_target_verdict_rejects_the_whole_report(self):
        first = dict(offset=0, type=4, symbol='synthetic_call', ok=True)
        for verdict in (False, None, 'true'):
            with self.subTest(verdict=verdict):
                second = dict(offset=4, type=4, symbol='synthetic_other')
                if verdict is not None:
                    second['ok'] = verdict
                result = self.compare([0x0c040400, 0x0c040800], [0x0c040400, 0x0c040c00], [first, second])
                self.assertFalse(result['relocations']['passed'])

    def test_unresolved_target_still_rejects_even_if_other_checks_pass(self):
        record = dict(offset=0, type=4, symbol='synthetic_call', ok=True)
        result = self.compare([0x0c040400, 0], [0x0c040400, 0], [record], unresolved=['0x0'])
        self.assertFalse(result['relocations']['passed'])
        self.assertEqual(result['relocations']['unresolved'][0]['offset'], 0)

    def test_no_relocations_is_valid_but_does_not_approve_a_byte_mismatch(self):
        result = self.compare([0x03e00008, 0x2402002a], [0x03e00008, 0x2402002b], [])
        self.assertTrue(result['relocations']['passed'])
        self.assertFalse(result['byteMatch'])


if __name__ == '__main__':
    unittest.main()
