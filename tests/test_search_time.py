"""Cumulative queue accounting with synthetic references/tools only."""
from pathlib import Path
from types import SimpleNamespace
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import auto_jobs as jobs
import local_process
import search_time
from project_claims import ClaimAPI
from tests import test_project_engine as fixtures
from tests.elf_fixture import elf


class Clock:
    def __init__(self):
        self.value = 100.0

    def monotonic(self):
        return self.value


class SearchTimeTests(unittest.TestCase):
    def fixture(self, *, max_attempts=30, seconds=90, seed=41):
        project = fixtures.ProjectTests(methodName='runTest'); project.setUp()
        self.addCleanup(project.doCleanups)
        manager = jobs.Manager(project.root)
        session = manager.create([project.name], dict(workers=1, maxAttempts=max_attempts, seconds=seconds),
                                 seeds={project.name: project.source(seed)})
        folder = manager.folder(session['id']); directory = folder / 'jobs' / project.name
        return SimpleNamespace(project=project, manager=manager, engine=manager.engine, session=session,
                               pins=jobs.read(folder / 'session.json')['pins'], folder=folder,
                               directory=directory, path=directory / 'job.json')

    def run_worker(self, f, clock):
        with patch('auto_jobs.AutoEngine', return_value=f.engine), patch.object(search_time, 'time', clock):
            jobs.search_job(f.manager, f.session['id'], f.project.name)
        return jobs.read(f.path)

    def test_new_queue_has_no_elapsed_and_real_compiler_sees_active_checkpoint(self):
        f = self.fixture(seed=42); before = jobs.read(f.path)
        self.assertNotIn('elapsed', before)
        self.assertEqual(before['timeAccounting'], search_time.initial(f.session['options'], f.pins))
        original = f.engine.compare
        def compare(*args, **kwargs):
            persisted = jobs.read(f.path)
            self.assertTrue(persisted['timeAccounting']['active'])
            self.assertEqual(persisted['timeAccounting']['attempts'], 1)
            return original(*args, **kwargs)
        f.engine.compare = compare
        final = self.run_worker(f, Clock())
        self.assertTrue(final['best']['technicalMatch'])
        self.assertFalse(final['timeAccounting']['active'])
        self.assertEqual(final['timeAccounting']['segments'], 1)

    def two_segments(self, *, max_attempts=30, costs=(30, 60)):
        f = self.fixture(max_attempts=max_attempts); clock = Clock(); calls = []
        original = f.engine.compare
        def compare(name, source, **kwargs):
            calls.append((source, kwargs['timeout']))
            result = original(name, source, **kwargs)
            clock.value += costs[len(calls) - 1]
            if len(calls) == 1:
                (f.folder / 'stop.request').write_text('explicit test pause')
            return result
        f.engine.compare = compare
        def children(_rules, source):
            value = 43 if 'return 41;' in source else 42
            return [('synthetic-next', f.project.source(value))]
        with patch.object(jobs.Variants, 'generate', children):
            first = self.run_worker(f, clock)
            self.assertEqual(first['status'], 'queued', first)
            self.assertEqual(first['attempts'], 1)
            self.assertEqual(first['timeAccounting']['spent'], costs[0])
            first_started = first['started']
            old_log = (f.directory / 'attempts.jsonl').read_bytes()
            (f.folder / 'stop.request').unlink()
            clock.value += 10000  # Paused wall time consumes no search time.
            final = self.run_worker(f, clock)
        self.assertEqual(final['started'], first_started)
        self.assertEqual(final['timeAccounting']['segments'], 2)
        self.assertEqual(final['attempts'], 2)  # Cached root costs no new attempt.
        self.assertEqual(len(calls), 2)  # The unsuccessful first source is not rebuilt.
        self.assertEqual([c[1] for c in calls], [90, 90 - costs[0]])
        self.assertTrue((f.directory / 'attempts.jsonl').read_bytes().startswith(old_log))
        self.assertEqual(ClaimAPI(f.project.root).holder(f.project.name), f.session['owner'])
        self.assertEqual(jobs.read(f.folder / 'session.json')['options'], f.session['options'])
        return final

    def test_two_segments_share_original_ninety_seconds_and_negative_cache(self):
        final = self.two_segments()
        self.assertEqual(final['timeAccounting']['spent'], 90)
        self.assertEqual(final['elapsed'], 90)
        self.assertEqual(final['reason'], 'Zeitlimit erreicht')

    def test_attempt_limit_is_not_refunded_by_pause_or_cached_seed(self):
        final = self.two_segments(max_attempts=2, costs=(1, 1))
        self.assertEqual(final['reason'], 'Versuchslimit erreicht')
        self.assertEqual(final['timeAccounting']['spent'], 2)

    def test_cpu_variant_generation_consumes_the_same_pass_time(self):
        f = self.fixture(); clock = Clock()
        def children(_rules, source):
            clock.value += 90
            return [('too-late', f.project.source(42))]
        with patch.object(jobs.Variants, 'generate', children):
            final = self.run_worker(f, clock)
        self.assertEqual(final['attempts'], 1)
        self.assertEqual(final['timeAccounting']['spent'], 90)
        self.assertEqual(final['reason'], 'Zeitlimit erreicht')

    def test_exhausted_pass_runs_no_tools_and_keeps_claim(self):
        f = self.fixture(); job = jobs.read(f.path)
        job['timeAccounting'].update(spent=90, attempts=1, segments=1)
        job.update(attempts=1, elapsed=90, started='first-segment')
        jobs.write(f.path, job)
        with patch.object(f.engine, 'compare', side_effect=AssertionError('No budget')), \
             patch('auto_jobs.rough.decompile', side_effect=AssertionError('No budget')):
            final = self.run_worker(f, Clock())
        self.assertEqual(final['reason'], 'Zeitlimit erreicht')
        self.assertEqual(final['attempts'], 1)
        self.assertEqual(ClaimAPI(f.project.root).holder(f.project.name), f.session['owner'])

    def test_claim_lost_after_build_never_starts_a_child_or_releases_foreign_owner(self):
        f = self.fixture(); api = ClaimAPI(f.project.root); original = f.engine.compare
        def compare(*args, **kwargs):
            result = original(*args, **kwargs)
            api.release(f.session['owner'], f.project.name)
            self.assertTrue(api.claim('foreign-test-owner', f.project.name))
            return result
        f.engine.compare = compare
        final = self.run_worker(f, Clock())
        self.assertEqual(final['attempts'], 1)
        self.assertIn('Reservierung verloren', final['reason'])
        self.assertEqual(api.holder(f.project.name), 'foreign-test-owner')

    def test_executed_legacy_or_active_crash_fails_closed_without_reinterpreting_evidence(self):
        for change in ('legacy', 'legacy-preflight-only', 'active', 'wrong-version', 'bad-attempts', 'nan', 'null'):
            with self.subTest(change=change):
                f = self.fixture(); job = jobs.read(f.path)
                if change.startswith('legacy'):
                    job.pop('timeAccounting')
                    job.update(started='original-start', elapsed=89)
                    if change == 'legacy': job.update(attempts=2)
                elif change == 'active': job['timeAccounting'].update(active=True)
                elif change == 'wrong-version': job['timeAccounting']['version'] = 999
                elif change == 'bad-attempts': job['timeAccounting']['attempts'] = 1
                elif change == 'nan': job['timeAccounting']['spent'] = float('nan')
                else: job['timeAccounting'] = None
                jobs.write(f.path, job)
                evidence = f.directory / 'attempts.jsonl'; evidence.write_bytes(b'original evidence\n')
                claim = ClaimAPI(f.project.root).path(f.project.name); claim_bytes = claim.read_bytes()
                with patch.object(f.engine, 'compare', side_effect=AssertionError('Unknown time')), \
                     patch('auto_jobs.rough.decompile', side_effect=AssertionError('Unknown time')):
                    final = self.run_worker(f, Clock())
                self.assertEqual(final['status'], 'error')
                self.assertEqual(final.get('elapsed'), job.get('elapsed'))
                self.assertEqual(final.get('started'), job.get('started'))
                self.assertEqual(json.dumps(final.get('timeAccounting'), sort_keys=True),
                                 json.dumps(job.get('timeAccounting'), sort_keys=True))
                self.assertEqual(final['attempts'], job['attempts'])
                self.assertEqual(evidence.read_bytes(), b'original evidence\n')
                self.assertEqual(claim.read_bytes(), claim_bytes)

    def test_pristine_legacy_queue_can_start_from_zero(self):
        f = self.fixture(seed=42); job = jobs.read(f.path); job.pop('timeAccounting'); jobs.write(f.path, job)
        self.assertNotIn('elapsed', jobs.read(f.path))
        self.assertTrue(self.run_worker(f, Clock())['best']['technicalMatch'])

    def test_unknown_time_message_translates_without_changing_evidence(self):
        from presentation_language import markdown
        english = markdown(search_time.UNKNOWN, 'en')
        self.assertIn('explicitly start a new search pass', english)
        self.assertEqual(markdown(english, 'de'), search_time.UNKNOWN)

    def test_legacy_artifact_without_metadata_is_still_not_proof_of_zero_time(self):
        f = self.fixture(); job = jobs.read(f.path); job.pop('timeAccounting'); jobs.write(f.path, job)
        (f.directory / 'rough.json').write_text('{}')
        final = self.run_worker(f, Clock())
        self.assertEqual(final['status'], 'error')
        self.assertNotIn('elapsed', final)

    def test_explicit_retry_archives_unknown_pass_and_initializes_a_new_budget(self):
        f = self.fixture(); job = jobs.read(f.path); job.pop('timeAccounting')
        job.update(status='error', started='legacy-original', elapsed=89, attempts=2)
        jobs.write(f.path, job); old = f.path.read_bytes()
        log = f.directory / 'attempts.jsonl'; log.write_bytes(b'original negative history\n')
        with patch.object(f.manager, 'start', return_value={'explicit': True}):
            self.assertEqual(f.manager.retry(f.session['id']), {'explicit': True})
        fresh = jobs.read(f.path); archive = Path(fresh['previousPass'])
        self.assertEqual(archive.read_bytes(), old)
        self.assertEqual((archive.parent / 'attempts.jsonl').read_bytes(), b'original negative history\n')
        self.assertEqual(fresh['timeAccounting'], search_time.initial(f.session['options'], f.pins))
        self.assertNotIn('elapsed', fresh)
        self.assertEqual(fresh['previousAttempts'], 2)
        self.assertEqual(ClaimAPI(f.project.root).holder(f.project.name), f.session['owner'])
        self.assertEqual(jobs.read(f.folder / 'session.json')['options'], f.session['options'])
        counts = []; original = f.engine.compare
        def compare(*args, **kwargs):
            counts.append(jobs.read(f.path)['attempts'])
            return original(*args, **kwargs)
        f.engine.compare = compare
        final = self.run_worker(f, Clock())
        self.assertEqual(counts[0], 1)
        self.assertEqual(final['attempts'], len(counts))
        self.assertFalse(final['timeAccounting']['active'])
        self.assertEqual(archive.read_bytes(), old)

    def test_handed_off_and_deferred_jobs_remain_byte_identical(self):
        for change in (dict(status='handed_off'), dict(deferred=True)):
            f = self.fixture(); job = jobs.read(f.path); job.update(change); jobs.write(f.path, job)
            before = f.path.read_bytes()
            self.run_worker(f, Clock())
            self.assertEqual(f.path.read_bytes(), before)

    def test_selective_retry_cannot_rebind_another_paused_pass_to_new_compiler_pins(self):
        project = fixtures.ProjectTests(methodName='runTest'); project.setUp(); self.addCleanup(project.doCleanups)
        other = 'func_00100008'
        project.write('baserom/main.elf', elf(project.words * 2, executable=True))
        project.settings['programs']['main']['sha256'] = project.sha('baserom/main.elf')
        project.inventory.append(dict(name=other, placements=[dict(program='main', section='.text', address='0x00100008', size=8)]))
        project.save(); manager = jobs.Manager(project.root)
        session = manager.create([project.name, other], seeds={project.name: project.source(41), other: project.source(41).replace(project.name, other)})
        folder = manager.folder(session['id']); path = folder / 'jobs' / project.name / 'job.json'
        old_pins = jobs.read(folder / 'session.json')['pins']
        result = manager.engine.compare(project.name, project.source(41), owner=session['owner'], with_lines=False)
        self.assertIn('compiledSize', result)
        record = dict(attempt=1, snapshot=result['snapshot'], sourceHash=result['sourceHash'],
                      objectHash=result['objectHash'], score=jobs.similarity(result), technicalMatch=False, verdict=result['verdict'])
        jobs.write(Path(result['snapshot']).with_name('result.json'), result)
        log = path.with_name('attempts.jsonl'); log.write_text(json.dumps(record) + '\n')
        paused = jobs.read(path); paused.update(attempts=1, elapsed=3, started='first-segment', best=record, lastAttempt=record)
        paused['timeAccounting'].update(spent=3, attempts=1, segments=1); jobs.write(path, paused)
        other_path = folder / 'jobs' / other / 'job.json'; deferred = jobs.read(other_path)
        deferred.update(status='blocked', deferred=True); jobs.write(other_path, deferred)
        snapshot_bytes = Path(result['snapshot']).read_bytes(); log_bytes = log.read_bytes()
        compiler = project.root / 'tools/compile.py'; compiler.write_bytes(compiler.read_bytes() + b'\n# deliberately changed tool input\n')
        project.settings['inputs']['tools/compile.py'] = project.sha('tools/compile.py'); project.save(); manager._engine = None
        with patch.object(manager, 'start', return_value=None):
            manager.retry(session['id'], deferred_only=True)
        data = jobs.read(folder / 'session.json')
        self.assertNotEqual(search_time.input_identity(old_pins), search_time.input_identity(data['pins']))
        self.assertEqual(jobs.reusable_attempts(folder, data, project.name), {})
        with patch('auto_jobs.AutoEngine', return_value=manager.engine), \
             patch.object(manager.engine, 'compare', side_effect=AssertionError('Unselected pass must not restart')):
            jobs.search_job(manager, session['id'], project.name)
        rejected = jobs.read(path)
        self.assertEqual(rejected['status'], 'error')
        self.assertEqual(rejected['timeAccounting'], paused['timeAccounting'])
        self.assertEqual(rejected['elapsed'], 3)
        self.assertEqual(rejected['attempts'], 1)
        self.assertEqual(Path(result['snapshot']).read_bytes(), snapshot_bytes)
        self.assertEqual(log.read_bytes(), log_bytes)
        fresh_other = jobs.read(other_path)
        self.assertEqual(fresh_other['timeAccounting']['inputs'], search_time.input_identity(data['pins']))
        self.assertEqual(fresh_other['timeAccounting']['spent'], 0)
        self.assertNotIn('elapsed', fresh_other)
        self.assertEqual(ClaimAPI(project.root).holder(project.name), session['owner'])
        # A later explicit retry must not launder A's old evidence through an
        # archive whose global session pins were already replaced by B's retry.
        with patch.object(manager, 'start', return_value=None):
            manager.retry(session['id'])
        newest = jobs.read(folder / 'session.json')
        self.assertEqual(jobs.reusable_attempts(folder, newest, project.name), {})
        self.assertEqual(Path(result['snapshot']).read_bytes(), snapshot_bytes)


class ToolDeadlineTests(unittest.TestCase):
    def budget(self, clock, seconds=10):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        settings = dict(seconds=seconds, maxAttempts=30)
        job = dict(attempts=0, timeAccounting=search_time.initial(settings))
        budget = search_time.Budget(job, Path(temporary.name), settings)
        budget.start()
        return budget

    def test_multiple_probes_setup_and_compile_share_deadline_and_do_not_escape_context(self):
        clock = Clock()
        with patch.object(search_time, 'time', clock):
            budget = self.budget(clock)
            with budget.tools():
                self.assertEqual(search_time.tool_timeout('first-probe', 10), 10)
                clock.value += 6
                self.assertEqual(search_time.tool_timeout('second-probe', 10), 4)
                clock.value += 4
                with self.assertRaises(subprocess.TimeoutExpired):
                    local_process.run(['no-launch'], cwd='.', timeout=10)
            self.assertEqual(search_time.tool_timeout('manual-outside-job', 30), 30)

    def test_claim_git_check_obeys_deadline_and_still_rejects_failed_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / '.git').mkdir(); clock = Clock(); seen = []
            def launch(*args, **kwargs):
                process = SimpleNamespace(returncode=0)
                def communicate(*, timeout):
                    seen.append(timeout); return (str(root / '.git'), '')
                process.communicate = communicate
                return process
            job = SimpleNamespace(name='synthetic-job', close=lambda: None)
            with patch.object(search_time, 'time', clock), patch('local_process.subprocess.Popen', side_effect=launch) as popen, \
                 patch('windows_job.Job', return_value=job):
                budget = self.budget(clock)
                with budget.tools():
                    clock.value += 9
                    self.assertEqual(ClaimAPI(root).DIR, (root / 'build/claims').resolve())
                    self.assertEqual(seen, [1])
                    clock.value += 1
                    with self.assertRaises(subprocess.TimeoutExpired):
                        ClaimAPI(root)
                self.assertEqual(popen.call_count, 1)
            failed = subprocess.CompletedProcess(['git'], 128, '', 'failure')
            with patch('local_process.run', return_value=failed):
                with self.assertRaises(subprocess.CalledProcessError):
                    ClaimAPI(root)

    def test_rac3_placement_workers_use_the_shared_tool_deadline(self):
        # Calls the actual adapter method with synthetic worker output only.
        # Its former direct subprocess.run(90) bypassed the shared deadline.
        from RAC3_PAL.rac3_engine import Engine
        with tempfile.TemporaryDirectory() as tmp:
            clock = Clock(); seen = []; engine = object.__new__(Engine)
            engine.root = Path(tmp); engine.program_target = lambda _: 'synthetic-target'
            def launch(*args, **kwargs):
                process = SimpleNamespace(pid=123, returncode=0, poll=lambda: None, kill=lambda: None)
                def communicate(*, timeout):
                    seen.append(timeout)
                    if timeout < 6:
                        raise subprocess.TimeoutExpired('synthetic-placement', timeout)
                    clock.value += 6
                    return ('{"ok": true}', '')
                process.communicate = communicate
                return process
            job = SimpleNamespace(name='synthetic-job', terminate=lambda: None, close=lambda: None)
            with patch.object(search_time, 'time', clock), patch('local_process.subprocess.Popen', side_effect=launch), \
                 patch('local_process.os.killpg', create=True), patch('windows_job.Job', return_value=job):
                budget = self.budget(clock)
                with budget.tools():
                    place = ('synthetic', '.text', 0, 8)
                    self.assertTrue(engine._compare_place('func_00100000', 'synthetic source', place,
                                                         Path(tmp) / 'one', 90)['ok'])
                    with self.assertRaises(subprocess.TimeoutExpired):
                        engine._compare_place('func_00100000', 'synthetic source', place, Path(tmp) / 'two', 90)
            self.assertEqual(seen[:2], [10, 4])

    def test_popen_startup_is_charged_before_communicate_and_expired_child_is_cleaned_up(self):
        for launch_cost, expected in ((6, 4), (11, None)):
            with self.subTest(launch_cost=launch_cost):
                clock = Clock(); process = SimpleNamespace(pid=123, returncode=0)
                seen = []
                def communicate(*, timeout):
                    seen.append(timeout); return ('', '')
                process.communicate = communicate
                process.poll = lambda: None
                process.kill = lambda: None
                def launch(*args, **kwargs):
                    clock.value += launch_cost
                    return process
                job = SimpleNamespace(name='synthetic-job', terminate=lambda: None, close=lambda: None)
                with patch.object(search_time, 'time', clock), patch('local_process.subprocess.Popen', side_effect=launch), \
                     patch('local_process.os.killpg', create=True), patch('windows_job.Job', return_value=job):
                    budget = self.budget(clock)
                    with budget.tools():
                        if expected is None:
                            with self.assertRaises(subprocess.TimeoutExpired):
                                local_process.run(['synthetic-tool'], cwd='.', timeout=90, text=True)
                            self.assertEqual(seen, [2])  # Bounded cleanup only.
                        else:
                            local_process.run(['synthetic-tool'], cwd='.', timeout=90, text=True)
                            self.assertEqual(seen, [expected])


if __name__ == '__main__':
    unittest.main()
