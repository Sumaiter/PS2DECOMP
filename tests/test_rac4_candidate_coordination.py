"""RAC4 discovery must show free functions without granting a matching scope."""
import copy
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import auto_jobs
import project_coordination
from RAC4_PAL import assert_publication_ready, public_reservations, upstream_matches


class RAC4CandidateCoordinationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.free = 'func_00100000'
        self.own = 'func_00100010'
        self.pending = 'func_00100020'
        self.pending_overlap = 'func_00100024'
        self.foreign = 'func_00100030'
        self.overlap = 'func_00100034'
        self.integrated = 'func_00100050'
        self.integrated_overlap = 'func_00100054'
        self.released = 'func_00100060'
        self.other_region = 'func_00100070'
        self.ambiguous = 'func_00100080'
        self.unknown_region = 'func_00100090'
        self.clone = 'func_L00_00200000'
        names = [self.free, self.own, self.pending, self.pending_overlap,
                 self.foreign, self.overlap, self.integrated,
                 self.integrated_overlap, self.released, self.other_region,
                 self.ambiguous, self.unknown_region]
        places = {name: ('main', '.text', int(name[5:], 16), 8, 'function') for name in names}
        places[self.clone] = ('level_00', '.text', 0x200000, 8, 'function')
        self.holders = {}
        self.engine = SimpleNamespace(
            root=self.root, version='pal', program='main', places=places,
            config={'adapter': 'RAC4_PAL', 'coordination': {'mode': 'native'}},
            remnants=set(), contracts={}, place=lambda name: places[name],
            all_places=lambda name: ([places[name][:4], ('level_01', '.text', 0x200000, 8)]
                                     if name == self.clone else [places[name][:4]]),
            inventory_state=lambda: ({}, set(), {}, self.holders),
            environment=lambda: 'synthetic',
            check=SimpleNamespace(disc_bytes=lambda place: struct.pack('<II', 0x03e00008, 0x2402002a)),
        )
        self.entries = [
            self.reservation(self.own, owner='CurrentContributor'),
            self.reservation(self.pending, owner='CurrentContributor', status='verified'),
            self.reservation(self.foreign), self.reservation(self.released, status='released'),
            self.reservation(self.other_region, version='us'),
            self.reservation(self.ambiguous, status='ambiguous'),
            self.reservation(self.unknown_region, version=None),
        ]
        self.matches = SimpleNamespace(names={self.integrated}, matches=[
            SimpleNamespace(places=[places[self.integrated][:4]]),
            SimpleNamespace(places=[('level_01', '.text', 0x200004, 8)]),
        ])
        snapshot = patch.object(public_reservations, 'fetch_reservations', side_effect=lambda root:
                                {'complete': True, 'entries': copy.deepcopy(self.entries)})
        self.snapshot = snapshot.start()
        self.addCleanup(snapshot.stop)
        api = patch.object(public_reservations, '_api', return_value=({'login': 'currentcontributor'}, {}))
        api.start()
        self.addCleanup(api.stop)
        upstream = patch.object(upstream_matches, 'fetch_matches', return_value=self.matches)
        self.upstream = upstream.start()
        self.addCleanup(upstream.stop)

    @staticmethod
    def reservation(name, owner='AnotherContributor', status='active', version='pal'):
        return dict(name=name, owner=owner, status=status, version=version)

    def candidates(self):
        return {row['name'] for row in auto_jobs.plan(self.engine, settings={'minBytes': 8, 'maxBytes': 1024})}

    def test_discovery_lists_free_functions_beyond_own_scope_and_excludes_pending_matches(self):
        self.assertEqual(self.candidates(), {self.free, self.own, self.released, self.other_region})
        self.assertFalse((self.root / 'build').exists(), 'Listing candidates must not claim or start work.')
        project_coordination.assert_free(self.engine, [self.own])
        with self.assertRaisesRegex(ValueError, 'outside the allowed contributor scope'):
            project_coordination.assert_free(self.engine, [self.free])

    def test_empty_own_scope_still_lists_free_functions_but_start_takes_no_claim(self):
        self.entries = [entry for entry in self.entries if entry['owner'] != 'CurrentContributor']
        self.assertIn(self.free, self.candidates())
        manager = auto_jobs.Manager(self.root)
        manager._engine = self.engine
        with self.assertRaisesRegex(ValueError, 'outside the allowed contributor scope'):
            manager.create([self.free])
        self.assertFalse((self.root / 'build').exists())

    def test_local_claims_still_hide_functions_after_the_public_filter(self):
        self.holders[self.free] = 'ps2decomp-auto-synthetic'
        self.assertNotIn(self.free, self.candidates())

    def test_new_foreign_reservation_is_rechecked_after_discovery(self):
        self.assertIn(self.own, self.candidates())
        self.entries.append(self.reservation(self.own))
        with self.assertRaises(ValueError):
            project_coordination.assert_free(self.engine, [self.own])
        self.assertNotIn(self.own, self.candidates())

    def test_missing_reservations_or_upstream_coverage_fail_closed(self):
        for mode in ('public-first', 'local-first'):
            self.engine.config['coordination']['workflow'] = mode
            for module, name in ((public_reservations, 'fetch_reservations'),
                                 (upstream_matches, 'fetch_matches')):
                with self.subTest(workflow=mode, service=name), patch.object(module, name,
                        side_effect=ValueError('Fresh coordination unavailable')):
                    with self.assertRaisesRegex(ValueError, 'Fresh coordination unavailable'):
                        self.candidates()
                    with self.assertRaisesRegex(ValueError, 'Fresh coordination unavailable'):
                        project_coordination.assert_free(self.engine, [self.own])
                    with self.assertRaisesRegex(ValueError, 'Fresh coordination unavailable'):
                        assert_publication_ready(self.engine, [self.own])

    def test_discovery_does_not_bypass_native_mode(self):
        self.engine.config['coordination']['mode'] = 'local'
        with self.assertRaisesRegex(ValueError, 'native coordination gate'):
            self.candidates()

    def test_explicit_local_first_allows_free_functions_without_a_public_scope(self):
        self.engine.config['coordination']['workflow'] = 'local-first'
        self.entries = [entry for entry in self.entries if entry['owner'] != 'CurrentContributor']
        project_coordination.assert_free(self.engine, [self.free, self.own])
        self.assertFalse((self.root / 'build').exists())
        for name in (self.foreign, self.overlap, self.integrated, self.integrated_overlap,
                     self.clone, self.ambiguous, self.unknown_region):
            with self.subTest(name=name), self.assertRaises(ValueError):
                project_coordination.assert_free(self.engine, [name])

    def test_local_first_start_rechecks_new_foreign_claims(self):
        self.engine.config['coordination']['workflow'] = 'local-first'
        self.assertIn(self.free, self.candidates())
        self.entries.append(self.reservation(self.free))
        with self.assertRaises(ValueError):
            project_coordination.assert_free(self.engine, [self.free])

    def test_local_first_publication_still_requires_own_scope_and_rechecks_conflicts(self):
        self.engine.config['coordination']['workflow'] = 'local-first'
        project_coordination.assert_free(self.engine, [self.free])
        with self.assertRaisesRegex(ValueError, 'own public reservation'):
            assert_publication_ready(self.engine, iter([self.free]))
        proposal = self.root / 'build/coordination/reservation-comment-proposal.txt'
        self.assertIn(self.free, proposal.read_text(encoding='utf-8'))
        self.entries.append(self.reservation(self.free, owner='CurrentContributor'))
        assert_publication_ready(self.engine, iter([self.free]))
        self.entries.append(self.reservation(self.free))
        with self.assertRaises(ValueError):
            assert_publication_ready(self.engine, iter([self.free]))

    def test_unknown_workflow_and_function_fail_closed_before_publication(self):
        for mode in ('local-first', 'public-first'):
            self.engine.config['coordination']['workflow'] = mode
            with self.assertRaisesRegex(ValueError, 'Unknown selected functions'):
                assert_publication_ready(self.engine, iter(['func_00DEAD00']))
        self.engine.config['coordination']['workflow'] = 'typo'
        for check in (lambda: self.candidates(),
                      lambda: project_coordination.assert_free(self.engine, [self.free]),
                      lambda: assert_publication_ready(self.engine, [self.own])):
            with self.assertRaisesRegex(ValueError, 'coordination.workflow'):
                check()

    def test_publication_capacity_includes_other_versions_and_existing_scopes(self):
        self.engine.config['coordination']['workflow'] = 'local-first'
        self.entries.extend(self.reservation('func_%08X' % (0x300000 + i * 8),
                            owner='CurrentContributor', version='us') for i in range(498))
        with self.assertRaisesRegex(ValueError, 'At most 500 unfinished functions'):
            assert_publication_ready(self.engine, [self.free])
        self.assertFalse((self.root / 'build').exists())

    def test_local_first_manual_and_queue_claims_keep_the_same_lifecycle(self):
        from project_claims import ClaimAPI
        from project_engine import Engine
        from tests.test_project_engine import ProjectTests
        fixture = ProjectTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        engine = Engine(fixture.root)
        engine.version = 'pal'
        engine.config.update(adapter='RAC4_PAL', coordination=dict(mode='native', workflow='local-first'))
        manager = auto_jobs.Manager(fixture.root)
        manager._engine = engine
        self.entries = []
        self.matches.names.clear()
        self.matches.matches.clear()
        manual = engine.dispatch(dict(op='compare', name=fixture.name, source=fixture.source()))
        self.assertTrue(manual['byteMatch'], manual['log'])
        claims = ClaimAPI(fixture.root)
        self.assertIsNone(claims.holder(fixture.name))
        session = manager.create([fixture.name], dict(workers=1, maxAttempts=2, seconds=15),
                                 seeds={fixture.name: fixture.source()})
        self.assertEqual(claims.holder(fixture.name), session['owner'])
        with self.assertRaises(ValueError):
            engine.dispatch(dict(op='compare', name=fixture.name, source=fixture.source()))
        with patch('auto_jobs.AutoEngine', return_value=engine):
            auto_jobs.search_job(manager, session['id'], fixture.name)
        job = auto_jobs.read(manager.folder(session['id']) / 'jobs' / fixture.name / 'job.json')
        self.assertEqual(job['status'], 'review_ready', job)
        self.assertTrue(job['best']['technicalMatch'])
        self.assertEqual(claims.holder(fixture.name), session['owner'])
        manager.handoff(session['id'], fixture.name)
        self.assertIsNone(claims.holder(fixture.name))
        self.assertNotIn(fixture.name, {row['name'] for row in manager.retry_plan(scope='all')})
        self.assertFalse((fixture.root / 'build/coordination/reservation-comment-proposal.txt').exists())


if __name__ == '__main__':
    unittest.main()
