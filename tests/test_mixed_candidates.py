"""Mixed selection must retain retry evidence and acquire one safe queue."""
from pathlib import Path
import unittest
from unittest.mock import patch

import auto_jobs as jobs
import retry_context
import search_time
from project_claims import ClaimAPI
from tests import test_project_engine as fixtures
from tests.elf_fixture import elf


class MixedCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.project = fixtures.ProjectTests(methodName='runTest')
        self.project.setUp(); self.addCleanup(self.project.doCleanups)
        self.root = self.project.root
        self.open = self.project.name
        self.free, self.error, self.busy, self.done, self.manual = [
            f'func_{0x100000 + i * 8:08X}' for i in range(1, 6)]
        self.names = [self.open, self.free, self.error, self.busy, self.done, self.manual]
        self.project.write('baserom/main.elf', elf(self.project.words * len(self.names), executable=True))
        self.project.settings['programs']['main']['sha256'] = self.project.sha('baserom/main.elf')
        self.project.inventory = [dict(name=name, placements=[dict(program='main', section='.text',
            address=0x100000 + i * 8, size=8)]) for i, name in enumerate(self.names)]
        self.project.save()
        self.manager = jobs.Manager(self.root)
        self.claims = ClaimAPI(self.root)
        self.limits = dict(workers=1, maxAttempts=1, seconds=5)

    def released(self, name, status='blocked', best=False, technical=False):
        session = self.manager.create([name], self.limits,
            seeds={name: self.project.source(41)} if best else None)
        if best:
            jobs.search_job(self.manager, session['id'], name)
        jobpath = self.manager.folder(session['id']) / 'jobs' / name / 'job.json'
        job = jobs.read(jobpath); job.update(status=status)
        if technical:
            snapshot = jobpath.with_name('synthetic.c'); snapshot.write_text('/* synthetic technical result */')
            job['lastAttempt'] = dict(technicalMatch=True, snapshot=str(snapshot), sourceHash=jobs.sha(snapshot))
        jobs.write(jobpath, job)
        self.manager.handoff(session['id'], name)
        return session

    def snapshot(self, session):
        folder = self.manager.folder(session['id'])
        return {p: p.read_bytes() for p in folder.rglob('*') if p.is_file()}

    def test_combined_plan_includes_released_deferred_and_errors_without_claiming(self):
        first = self.released(self.open)
        second = self.released(self.error, 'error')
        self.manager.create([self.busy], self.limits)
        self.released(self.done, 'review_ready', technical=True)
        draft = self.root / 'build/studio' / self.manual / 'draft.c'
        draft.parent.mkdir(parents=True); draft.write_text('manual draft')
        before = {**self.snapshot(first), **self.snapshot(second)}
        sessions = list(self.manager.directory.glob('*/session.json'))
        rows = self.manager.mixed_plan(100)
        self.assertEqual([row['name'] for row in rows[:2]], [row['name'] for row in self.manager.retry_plan(100, 'all')])
        self.assertEqual({row['name'] for row in rows}, {self.open, self.error, self.free})
        self.assertEqual({row['candidateKind'] for row in rows}, {'free', 'retry'})
        self.assertTrue(next(row for row in rows if row['name'] == self.open)['deferred'])
        self.assertTrue(next(row for row in rows if row['name'] == self.error)['analysisRequired'])
        self.assertEqual(len(rows), len({row['name'] for row in rows}))
        for name in (self.open, self.error, self.free): self.assertIsNone(self.claims.holder(name))
        self.assertEqual(list(self.manager.directory.glob('*/session.json')), sessions)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(draft.read_text(), 'manual draft')

    def test_limit_range_and_program_scope_apply_to_the_whole_list(self):
        self.released(self.open)
        self.assertEqual(self.manager.mixed_plan(1)[0]['name'], self.open)
        self.assertEqual(len(self.manager.mixed_plan(2)), 2)
        self.assertEqual(self.manager.mixed_plan(settings=dict(minBytes=12, maxBytes=16)), [])
        self.assertEqual(self.manager.mixed_plan(program_scope='overlays'), [])
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError): self.manager.mixed_plan(limit)

    def test_one_queue_preserves_old_best_and_history_and_has_per_job_budgets(self):
        old = self.released(self.open, best=True)
        before = self.snapshot(old)
        result = self.manager.create_retry([self.open, self.free], self.limits, include_unclaimed=True)
        folder = self.manager.folder(result['id']); data = jobs.read(folder / 'session.json')
        self.assertEqual(result['names'], [self.open, self.free])
        self.assertEqual(data['purpose'], 'unclaimed-and-open')
        self.assertEqual(data['retrySources'], {self.open: old['id']})
        self.assertEqual(len(data['history']), 1)
        retry, fresh = result['jobs']
        self.assertEqual(retry['previousAttempts'], 1)
        self.assertTrue(retry['retryDeferred'])
        self.assertEqual(retry['inputMode'], 'retry')
        original_best = jobs.read(self.manager.folder(old['id']) / 'jobs' / self.open / 'job.json')['best']
        self.assertEqual((folder / 'jobs' / self.open / 'seed.c').read_bytes(), Path(original_best['snapshot']).read_bytes())
        self.assertEqual(fresh['inputMode'], 'automatic')
        self.assertNotIn('previousSession', fresh)
        self.assertFalse((folder / 'jobs' / self.free / 'seed.c').exists())
        for job in result['jobs']:
            self.assertEqual(job['attempts'], 0)
            self.assertEqual(job['timeAccounting'], search_time.initial(result['options'], data['pins']))
            self.assertEqual(self.claims.holder(job['name']), result['owner'])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertTrue(jobs.prior_failed_sources(folder, data, self.open))
        self.assertEqual(jobs.prior_failed_sources(folder, data, self.free), set())

    def test_new_only_combined_queue_and_retry_only_default_keep_distinct_gates(self):
        with self.assertRaisesRegex(ValueError, 'früherer Suchlauf'):
            self.manager.create_retry([self.free], self.limits)
        result = self.manager.create_retry([self.free], self.limits, include_unclaimed=True)
        self.assertEqual(result['history'], [])
        self.assertEqual(result['retrySources'], {})
        self.assertEqual(result['jobs'][0]['inputMode'], 'automatic')

    def test_reserved_cases_and_recorded_technical_results_cannot_be_treated_as_new(self):
        self.released(self.done, 'review_ready', technical=True)
        self.assertNotIn(self.done, {row['name'] for row in self.manager.mixed_plan()})
        with self.assertRaisesRegex(ValueError, 'Technischer Treffer'):
            self.manager.create_retry([self.done, self.free], self.limits, include_unclaimed=True)
        self.claims.claim('foreign-owner', self.free)
        with self.assertRaises(ValueError):
            self.manager.create_retry([self.open, self.free], self.limits, include_unclaimed=True)
        self.assertEqual(self.claims.holder(self.free), 'foreign-owner')
        self.assertIsNone(self.claims.holder(self.open))

    def test_changed_old_result_rolls_back_all_new_claims(self):
        old = self.released(self.open)
        existing = list(self.manager.directory.glob('*/session.json'))
        path = self.manager.folder(old['id']) / 'jobs' / self.open / 'job.json'
        def progress(value):
            if value['stage'] == 'reserve' and value['done'] == 2:
                data = jobs.read(path); data['reason'] = 'changed by another reviewer'; jobs.write(path, data)
        with self.assertRaisesRegex(ValueError, 'Reservierung verändert'):
            self.manager.create_retry([self.open, self.free], self.limits, progress, include_unclaimed=True)
        self.assertIsNone(self.claims.holder(self.open)); self.assertIsNone(self.claims.holder(self.free))
        self.assertEqual(list(self.manager.directory.glob('*/session.json')), existing)

    def test_new_case_acquiring_history_during_claim_is_rejected(self):
        existing = list(self.manager.directory.glob('*/session.json'))
        def progress(value):
            if value['stage'] == 'reserve' and value['done'] == 1:
                sid = 'a' * 32
                jobs.write(self.manager.folder(sid) / 'session.json', dict(id=sid, created=jobs.now(), names=[self.free]))
                jobs.write(self.manager.folder(sid) / 'jobs' / self.free / 'job.json',
                    dict(name=self.free, status='handed_off', handedOff=jobs.now()))
        with self.assertRaisesRegex(ValueError, 'Reservierung verändert'):
            self.manager.create_retry([self.free], self.limits, progress, include_unclaimed=True)
        self.assertIsNone(self.claims.holder(self.free))
        self.assertEqual(len(list(self.manager.directory.glob('*/session.json'))), len(existing) + 1)

    def test_mixed_rpc_enforces_version_and_starts_one_session(self):
        engine = self.manager.engine
        for op in ('autoMixedPlan', 'autoMixedStart'):
            with self.subTest(op=op), self.assertRaisesRegex(ValueError, 'version configured'):
                engine.dispatch(dict(op=op, versions=['wrong-version'], names=[self.free]))
        engine._automatic = self.manager
        rows = engine.dispatch(dict(op='autoMixedPlan', limit=2, scope='main', versions=[engine.version]))
        self.assertEqual(len(rows), 2)
        with patch.object(self.manager, 'start', side_effect=self.manager.status) as start:
            result = engine.dispatch(dict(op='autoMixedStart', names=[self.free], settings=self.limits, versions=[engine.version]))
        start.assert_called_once_with(result['id'])

    def test_mixed_retry_context_follows_archived_lineage(self):
        folder = self.manager.directory / ('b' * 32); archive = folder / 'history/old'
        packet = archive / 'jobs' / self.open
        source = self.project.source(41)
        jobs.write(archive / 'session.json', dict(id='old-session'))
        jobs.write(packet / 'job.json', dict(name=self.open, inputMode='automatic'))
        (packet / 'seed.c').write_text(source)
        evidence = dict(ok=True, source=source, contextDeclarations=[])
        jobs.write(packet / 'rough.json', evidence)
        data = dict(purpose='unclaimed-and-open', history=[str(archive)])
        self.assertEqual(retry_context.origin(folder, data,
            dict(inputMode='retry', previousSession='old-session'), self.open), evidence)
        self.assertIsNone(retry_context.origin(folder, data, dict(inputMode='automatic'), self.free))


if __name__ == '__main__': unittest.main()
