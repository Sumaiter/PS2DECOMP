"""Synthetic sources/instructions only; original game artifacts stay local."""
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

from elftools.elf.elffile import ELFFile
from search_rules import Variants, source_key
from RAC4_PAL import ee296_profile as profile
from RAC4_PAL import ee296_leaf_walk as walk
from tests.elf_fixture import elf

FIXTURE = Path(__file__).parent / 'fixtures/project'
NAME = 'func_00100000'
SESSION = 'a' * 32
OWNER = 'ps2decomp-auto-' + SESSION
SOURCE = '''#include "common.h"
void *func_00100000(void *start) {
    void *next = start;
    void *last = NULL;
    if (next != NULL) {
        do {
            last = next;
            next = *(void **)((s8 *)last + 4);
        } while (next != NULL);
    }
    return last;
}
'''
# Independently authored read-only linked-node loop, including complete delays.
WORDS = (0x10800005, 0x0000102d, 0x0080102d, 0x8c840004,
         0x1480fffd, 0, 0x03e00008, 0)
RAW = struct.pack('<8I', *WORDS)


def empty_object():
    data = bytearray(elf(()))
    table = ELFFile(io.BytesIO(data)).get_section_by_name('.symtab')
    data[table['sh_offset'] + 16 + 12] = 0  # synthetic NOTYPE, no function/object
    return bytes(data)


class ShapeTests(unittest.TestCase):
    def setUp(self):
        self.rules = Variants(FIXTURE)

    def test_positive_uses_shape_not_names_addresses_or_scores(self):
        self.assertEqual(walk.analyze(self.rules, SOURCE)['loadOffsets'], [4])
        self.assertEqual(walk.native_shape(RAW, 0x100000), {'loadOffsets': [4]})
        changed = SOURCE.replace(NAME, 'arbitrary_walk').replace('start', 'input').replace('last', 'previous')
        self.assertEqual(walk.analyze(self.rules, changed)['name'], 'arbitrary_walk')
        self.assertEqual(walk.native_shape(RAW, 0x650020), {'loadOffsets': [4]})

    def test_explicit_flags_globals_calls_stores_and_absolute_addresses_reject(self):
        variants = [
            '/* cflags: -mno-split-addresses */\n' + SOURCE,
            SOURCE + '\n/* cflags: @ee296 -O2 -G0 */',
            SOURCE.replace('void *next = start;', 'void *next = (void *)0x123456;'),
            SOURCE.replace('next = *(void **)((s8 *)last + 4);', '*(void **)last = next;'),
            SOURCE.replace('next = *(void **)((s8 *)last + 4);', 'next = callback(last);'),
            SOURCE.replace('void *next = start;', 'volatile void *next = start;'),
            SOURCE.replace('void *next = start;', 'void *next = __builtin_frame_address(0);'),
            '#define hidden start\n' + SOURCE,
            SOURCE + '\nvoid *outside;',
            SOURCE.replace('while (next != NULL)', 'while (next < start)'),
            SOURCE.replace('void *next = start;', 'float next = 0;'),
            SOURCE.replace('return last;', 'return last + ;'),
            SOURCE.replace('#include "common.h"', ''),
            '#include "common.h"\n' + SOURCE,
        ]
        for source in variants:
            with self.subTest(source=source):
                self.assertIsNone(walk.analyze(self.rules, source))

    def test_real_header_type_and_macro_changes_fail_closed(self):
        for tail in ('#include "unknown.h"', '#define s8 float', 'extern int other;'):
            rules = Variants(FIXTURE)
            rules.header_text += '\n' + tail
            self.assertIsNone(walk.analyze(rules, SOURCE))
        for before, after in (('typedef signed char s8;', 'typedef volatile signed char s8;'),
                              ('typedef int s32;', 'typedef int s32;\ntypedef int s32;')):
            rules = Variants(FIXTURE)
            rules.header_text = rules.header_text.replace(before, after)
            rules.prelude = '\n'.join(line for line in rules.header_text.splitlines()
                                      if line.strip().startswith('typedef '))
            self.assertTrue(rules.plain_header())
            self.assertIsNone(walk.analyze(rules, SOURCE))

    def test_native_calls_stores_frames_gp_fragments_or_load_offset_disagreement(self):
        for index, word in ((2, 0x0c040000), (3, 0xac840004), (3, 0x8f840004),
                            (2, 0x27bdfff0), (2, 0x3c021234), (4, 0x1480fff0)):
            words = list(WORDS); words[index] = word
            with self.subTest(word=hex(word)):
                self.assertIsNone(walk.native_shape(struct.pack('<8I', *words), 0x100000))
        self.assertIsNone(walk.native_shape(RAW[:-4], 0x100000))
        self.assertIsNone(walk.native_shape(RAW, True))

    def fake_engine(self, raw=RAW):
        return SimpleNamespace(root=FIXTURE, place=lambda _: ('main', '.text', 0x100000, len(raw)),
            all_places=lambda _: [('main', '.text', 0x100000, len(raw))],
            assert_reference=lambda _: None, check=SimpleNamespace(disc_bytes=lambda _: raw))

    def test_source_gate_is_lazy_and_no_explicit_flags_are_overwritten(self):
        engine = SimpleNamespace(root=FIXTURE, place=lambda _: self.fail('Unexpected native read'))
        self.assertEqual(walk.generate_for_engine(engine, NAME, 'int f(void){return 0;}', rules=self.rules), [])
        self.assertEqual(walk.generate_for_engine(engine, NAME, '/* cflags: -O2 */\n' + SOURCE, rules=self.rules), [])

    def test_all_placements_and_offsets_must_agree_before_availability_probe(self):
        engine = self.fake_engine()
        engine.all_places = lambda _: [('main', '.text', 0x100000, 32), ('other', '.text', 0x300000, 32)]
        changed = list(WORDS); changed[3] = 0x8c840008
        engine.check.disc_bytes = lambda p: RAW if p[0] == 'main' else struct.pack('<8I', *changed)
        with patch.object(profile, 'inspect', side_effect=AssertionError('Unexpected ABI probe')):
            self.assertEqual(walk.generate_for_engine(engine, NAME, SOURCE, rules=self.rules), [])
        engine = self.fake_engine()
        source = SOURCE.replace('last + 4', 'last + 8')
        with patch.object(profile, 'inspect', side_effect=AssertionError('Unexpected ABI probe')):
            self.assertEqual(walk.generate_for_engine(engine, NAME, source, rules=self.rules), [])

    def test_single_candidate_preserves_exact_source_and_history_identity(self):
        engine = self.fake_engine()
        proof = dict(available=True, abiVerified=True, inputHashes={'pinned': 'hash'})
        with patch.object(profile, 'current_hashes', return_value={'pinned': 'hash'}), \
             patch.object(profile, 'pinned_session', return_value=5), \
             patch.object(profile, 'inspect', return_value=proof):
            children = walk.generate_for_engine(engine, NAME, SOURCE, rules=self.rules)
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0][1], '/* cflags: @ee296 -O2 -G0 */\n' + SOURCE)
        self.assertNotEqual(source_key(children[0][1]), source_key(SOURCE))
        self.assertEqual(walk.generate_for_engine(engine, NAME, children[0][1], rules=self.rules), [])


class PinAndProbeTests(unittest.TestCase):
    def setUp(self):
        profile._PROBES.clear()

    def session(self, root, pins):
        claim = root / 'build/claims' / NAME; claim.parent.mkdir(parents=True); claim.write_text(OWNER + ' time\n')
        folder = root / 'build/autodecomp' / SESSION
        job = folder / 'jobs' / NAME / 'job.json'; job.parent.mkdir(parents=True)
        job.write_text(json.dumps(dict(name=NAME, status='running', deferred=False,
            created='first-generation', inputMode='automatic', attempts=1, workerPid=os.getpid(),
            started=datetime.datetime.now(datetime.timezone.utc).isoformat())))
        data = dict(id=SESSION, owner=OWNER, version='pal', status='running', names=[NAME],
            options=dict(seconds=90), pins=pins)
        (folder/'session.json').write_text(json.dumps(data))
        return claim, folder, job

    def test_inputs_must_have_been_pinned_before_current_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); engine = SimpleNamespace(root=root, _ee296_fresh_jobs={(SESSION, NAME):
                dict(created='first-generation', captured=time.monotonic(), pid=os.getpid())})
            claim, folder, job = self.session(root, {'input': {'sha256': 'old'}})
            self.assertGreater(profile.pinned_session(engine, NAME, {'input': 'old'}, owner=OWNER), 0)
            for hashes in ({'input': 'new'}, {'later-installed': 'old'}):
                with self.assertRaises(ValueError): profile.pinned_session(engine, NAME, hashes)
            claim.write_text('foreign-owner time\n')
            with self.assertRaises(ValueError): profile.pinned_session(engine, NAME, {'input': 'old'})
            claim.write_text(OWNER + ' time\n')
            with self.assertRaises(ValueError): profile.pinned_session(engine, NAME, {'input': 'old'}, owner='another-owner')
            for change in (dict(status='handed_off'), dict(deferred=True), dict(started='2000-01-01T00:00:00+00:00')):
                data=json.loads(job.read_text());data.update(change);job.write_text(json.dumps(data))
                with self.assertRaises(ValueError): profile.pinned_session(engine, NAME, {'input': 'old'})

    def test_only_witnessed_first_execution_has_a_known_time_budget(self):
        for previous in ({}, {'started': '2000-01-01T00:00:00+00:00', 'elapsed': 89}, {'elapsed': 0},
                         {'previousPass': 'archived'}, {'workerPid': 123}):
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                claim, folder, path = self.session(root, {'input': {'sha256': 'old'}})
                first = dict(name=NAME, status='queued', deferred=False, created='first-generation',
                             inputMode='automatic', attempts=0)
                first.update(previous); path.write_text(json.dumps(first))
                engine = SimpleNamespace(root=root, _ee296_fresh_jobs=profile.fresh_jobs(root))
                # Shared search_job has overwritten started AND elapsed. There
                # need not have been any earlier compiler attempt or snapshot.
                first.update(status='running', attempts=1, workerPid=os.getpid(), elapsed=2,
                             started=datetime.datetime.now(datetime.timezone.utc).isoformat())
                path.write_text(json.dumps(first))
                if previous:
                    with self.assertRaises(ValueError): profile.pinned_session(engine, NAME, {'input': 'old'}, owner=OWNER)
                else:
                    self.assertGreater(profile.pinned_session(engine, NAME, {'input': 'old'}, owner=OWNER), 0)
                    engine._ee296_fresh_jobs.clear()
                    with self.assertRaises(ValueError): profile.pinned_session(engine, NAME, {'input': 'old'})

    def test_explicit_manual_file_profile_needs_own_claim_but_no_automatic_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manual_owner = 'rac4-editor-' + 'b' * 32
            claim = root / 'build/claims' / NAME; claim.parent.mkdir(parents=True)
            claim.write_text(manual_owner + ' time\n')
            engine = SimpleNamespace(root=root, manual_owner=manual_owner)
            proof = dict(available=True, abiVerified=True, inputHashes={'input': 'hash'})
            with patch.object(profile, 'current_hashes', return_value={'input': 'hash'}), \
                 patch.object(profile, 'inspect', return_value=proof), patch.object(profile, 'environment', return_value={}), \
                 patch.object(profile, 'pinned_session', side_effect=AssertionError('Manual claim is not an automatic job')):
                self.assertEqual(profile.comparison_environment(engine, NAME, profile.FLAGS, None, 5), ({}, 5))
                self.assertEqual(profile.comparison_environment(engine, NAME, profile.FLAGS, manual_owner, 5), ({}, 5))
                claim.write_text('foreign-owner time\n')
                with self.assertRaises(ValueError): profile.comparison_environment(engine, NAME, profile.FLAGS, None, 5)
                claim.unlink()
                with self.assertRaises(OSError): profile.comparison_environment(engine, NAME, profile.FLAGS, manual_owner, 5)

    def test_wall_clock_rollback_cannot_refund_monotonic_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.session(root, {'input': {'sha256': 'old'}})
            engine = SimpleNamespace(root=root, _ee296_fresh_jobs={(SESSION, NAME):
                dict(created='first-generation', captured=0, pid=os.getpid())})
            # UTC started is still "now"; monotonic time alone proves expiry.
            with patch.object(profile.time, 'monotonic', return_value=90):
                with self.assertRaisesRegex(ValueError, 'no time remaining'):
                    profile.pinned_session(engine, NAME, {'input': 'old'})

    def test_malformed_optional_job_metadata_cannot_break_engine_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claim, folder, path = self.session(root, {})
            path.write_text('[]')
            self.assertEqual(profile.fresh_jobs(root), {})

    def test_missing_or_changed_original_compiler_files_are_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'EE296_ROOT': tmp}):
            status=profile.inspect(Path(tmp))
            self.assertFalse(status['available'])
            self.assertFalse(status['abiVerified'])

    def test_actual_assembler_backend_and_specs_are_pinned_not_assumed_aliases(self):
        paths = {str(p).replace('\\', '/') for p in profile.required_inputs(FIXTURE)}
        self.assertTrue(any(p.endswith('/gcc/ee/bin/as.exe') for p in paths))
        self.assertTrue(any(p.endswith('/gcc/lib/gcc-lib/ee/2.95.3/specs') for p in paths))
        self.assertTrue(any(p.endswith('/local_process.py') for p in paths))
        self.assertFalse(any(p.endswith('/gcc/bin/ee-as.exe') for p in paths))
        for key in ('GCC_EXEC_PREFIX', 'COMPILER_PATH', 'GCC_SPECS', 'C_INCLUDE_PATH'):
            with self.subTest(key=key), patch.dict(os.environ, {key: 'unverified'}):
                with self.assertRaises(ValueError): profile.environment(FIXTURE)

    def test_disappearing_optional_profile_setting_or_header_fails_closed(self):
        for relative in ('build/isolated/ee-gcc2.96/workbench.json', 'include/removed.h'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.session(root, {str((root / relative).resolve()): {'sha256': 'before'}})
                engine = SimpleNamespace(root=root, _ee296_fresh_jobs={(SESSION, NAME):
                    dict(created='first-generation', captured=time.monotonic(), pid=os.getpid())})
                with self.assertRaisesRegex(ValueError, 'disappeared'):
                    profile.pinned_session(engine, NAME, {})

    def test_exact_adapter_marker_contract_does_not_expand_shared_flags(self):
        from RAC4_PAL.manual_engine import SAFE_FLAGS
        self.assertNotIn('@ee296', SAFE_FLAGS)
        for flags in (['@ee296'], ['@ee296', '-O2', '-G0', '-ffast-math'], ['-O2', '@ee296', '-G0']):
            with self.assertRaises(ValueError):
                profile.comparison_environment(SimpleNamespace(root=FIXTURE), NAME, flags, OWNER, 5)

    def test_profile_check_cannot_extend_original_comparison_timeout(self):
        from RAC4_PAL.manual_engine import Engine
        engine = object.__new__(Engine)
        engine.assert_access = lambda *args: None
        engine.place = lambda name: ('main', '.text', 0x100000, 32)
        engine.layout = SimpleNamespace(cflags=lambda source: list(profile.FLAGS))
        with patch.object(profile, 'comparison_environment', return_value=({}, 5)), \
             patch('RAC4_PAL.manual_engine.time.monotonic', side_effect=[0, 6]), \
             patch('local_process.run', side_effect=AssertionError('Budget already exhausted')):
            with self.assertRaises(ValueError):
                engine.compare(NAME, '/* cflags: @ee296 -O2 -G0 */\n' + SOURCE, timeout=5, owner=OWNER)

    def test_abi_probe_is_function_free_and_rejects_code_or_relocations(self):
        for raw, expected in ((empty_object(), True), (b'not an object', False), (elf((0x03e00008, 0)), False),
                              (elf((), relocations=((0, 4),)), False)):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                def run(command, **kwargs):
                    (root / command[3]).write_bytes(raw)
                    return SimpleNamespace(returncode=0, stdout='', stderr='')
                with patch.object(profile, 'current_hashes', return_value={'input':'hash'}), \
                     patch.object(profile, 'environment', return_value={}), patch('local_process.run', side_effect=run):
                    status=profile.inspect(root)
                self.assertEqual(status['available'], expected)
                self.assertNotIn('{', profile.ABI_PROBE)
                self.assertIn('sizeof(void *)==4', profile.ABI_PROBE)


if __name__ == '__main__':
    unittest.main()
