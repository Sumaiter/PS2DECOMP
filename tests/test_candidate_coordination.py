"""Read-only discovery, explicit local matching and fresh publication checks."""
import copy
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import auto_jobs
import project_coordination
from RAC3_PAL import rac3_coordination


class CandidateCoordinationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.free = 'func_00100000'
        self.other_free = 'func_00100008'
        self.foreign = 'func_00100010'
        self.overlap = 'func_00100014'
        self.integrated = 'func_00100020'
        names = [self.free, self.other_free, self.foreign, self.overlap, self.integrated]
        places = {name: ('main', '.text', int(name[5:], 16), 8, 'function') for name in names}
        owners = {name: 'src/' + name + '.c' for name in names}
        self.engine = SimpleNamespace(
            root=self.root, version='pal', program='main', places=places,
            owners=owners, owner_paths={name: {owners[name]} for name in names},
            config={'adapter': 'RAC3_PAL', 'coordination': {'mode': 'native'}},
            _matched={}, remnants=set(), contracts={},
            place=lambda name: places[name], all_places=lambda name: [places[name][:4]],
            inventory_state=lambda: ({}, set(), {}, {}), environment=lambda: 'synthetic',
            check=SimpleNamespace(disc_bytes=lambda place: struct.pack('<II', 0x03e00008, 0x2402002a)),
        )
        self.state = dict(account='synthetic-contributor', mine=[(set(), set())],
                          other=[(set(), {owners[self.foreign]})],
                          open=set(names) - {self.integrated}, revision='synthetic-upstream')
        snapshot = patch.object(rac3_coordination, 'snapshot', side_effect=lambda engine: copy.deepcopy(self.state))
        self.snapshot = snapshot.start()
        self.addCleanup(snapshot.stop)

    def test_empty_own_scope_still_lists_free_candidates_and_preserves_foreign_overlaps(self):
        self.assertEqual(project_coordination.blocked(self.engine), set(self.engine.places))
        blocked = project_coordination.blocked(self.engine, discovery=True)
        self.assertEqual(blocked, {self.foreign, self.overlap, self.integrated})
        progress = []
        rows = auto_jobs.plan(self.engine, limit=10, progress=progress.append)
        self.assertEqual({row['name'] for row in rows}, {self.free, self.other_free})
        self.assertTrue(any(row.get('blocked') == 3 for row in progress))
        self.assertFalse((self.root / 'build/claims').exists())
        self.assertFalse((self.root / 'build/autodecomp').exists())

    def test_visible_unclaimed_function_cannot_create_a_session_or_take_a_claim(self):
        manager = auto_jobs.Manager(self.root)
        manager._engine = self.engine
        self.assertIn(self.free, {row['name'] for row in auto_jobs.plan(self.engine)})
        with self.assertRaisesRegex(ValueError, 'outside the allowed contributor scope'):
            manager.create([self.free])
        self.assertFalse(manager.directory.exists())
        self.assertFalse((self.root / 'build/claims').exists())

    def test_retry_discovery_lists_free_results_but_retry_start_still_requires_ownership(self):
        manager = auto_jobs.Manager(self.root)
        manager._engine = self.engine
        manager._latest_jobs = lambda: dict.fromkeys(self.engine.places)
        manager._retry_entry = lambda *args, **kwargs: dict(
            data={'id': 'synthetic-session'},
            job={'status': 'handed_off', 'best': {'score': 70}, 'reason': 'unfinished'},
        )
        rows = manager.retry_plan(limit=10)
        self.assertEqual({row['name'] for row in rows}, {self.free, self.other_free})
        with self.assertRaises(ValueError):
            manager.create_retry([self.free])
        self.assertFalse(manager.directory.exists())
        self.assertFalse((self.root / 'build/claims').exists())

    def test_start_rechecks_current_ownership_and_new_foreign_claims(self):
        with self.assertRaises(ValueError):
            project_coordination.assert_free(self.engine, [self.free])
        self.state['mine'] = [(set(), {self.engine.owners[self.free]})]
        project_coordination.assert_free(self.engine, [self.free])
        self.state['other'].append((set(), {self.engine.owners[self.free]}))
        with self.assertRaises(ValueError):
            project_coordination.assert_free(self.engine, [self.free])
        self.assertGreaterEqual(self.snapshot.call_count, 3)

    def test_linked_boot_source_units_stay_reserved_during_discovery(self):
        linked = 'src/linked-boot.c'
        self.engine.owner_paths[self.free].add(linked)
        self.state['other'].append((set(), {linked}))
        self.assertIn(self.free, project_coordination.blocked(self.engine, discovery=True))

    def test_existing_own_scope_does_not_hide_free_scopes_from_discovery(self):
        self.state['mine'] = [(set(), {self.engine.owners[self.free]})]
        self.assertNotIn(self.free, project_coordination.blocked(self.engine))
        self.assertIn(self.other_free, project_coordination.blocked(self.engine))
        self.assertEqual({row['name'] for row in auto_jobs.plan(self.engine)}, {self.free, self.other_free})

    def test_native_adapter_without_discovery_hook_retains_its_allowlist(self):
        self.engine.config['adapter'] = 'Synthetic_PAL'
        self.engine.config['coordination']['workflow'] = 'local-first'
        adapter = SimpleNamespace(coordination_state=lambda engine: (set(), {self.free}))
        with patch.dict(sys.modules, {'Synthetic_PAL': adapter}):
            for discovery in (False, True):
                self.assertEqual(project_coordination.blocked(self.engine, discovery=discovery),
                                 set(self.engine.places) - {self.free})

    def test_generic_command_allowlist_is_not_relaxed_for_discovery(self):
        self.engine.config = {'adapter': 'generic', 'coordination': {
            'mode': 'command', 'command': ['synthetic'], 'workflow': 'local-first'}}
        result = subprocess.CompletedProcess([], 0, json.dumps(dict(complete=True, version='pal',
                                                                  blocked=[], allowed=[self.free])), '')
        with patch('local_process.run', return_value=result):
            for discovery in (False, True):
                self.assertEqual(project_coordination.blocked(self.engine, discovery=discovery),
                                 set(self.engine.places) - {self.free})

    def test_missing_fresh_coordination_fails_closed_in_discovery_and_start(self):
        self.snapshot.side_effect = ValueError('coordination unavailable')
        with self.assertRaisesRegex(ValueError, 'coordination unavailable'):
            auto_jobs.plan(self.engine)
        with self.assertRaisesRegex(ValueError, 'coordination unavailable'):
            project_coordination.assert_free(self.engine, [self.free])

    def test_native_discovery_does_not_bypass_coordination_mode(self):
        self.engine.config['coordination']['mode'] = 'local'
        with self.assertRaisesRegex(ValueError, 'native coordination gate'):
            project_coordination.blocked(self.engine, discovery=True)

    def local_first(self):
        self.engine.config['coordination']['workflow'] = 'local-first'

    def test_local_first_allows_free_functions_without_own_public_scope(self):
        self.local_first()
        expected = {self.foreign, self.overlap, self.integrated}
        for discovery in (False, True):
            self.assertEqual(project_coordination.blocked(self.engine, discovery=discovery), expected)
        project_coordination.assert_free(self.engine, [self.free, self.other_free])
        rac3_coordination.assert_free(self.engine, [self.free])
        self.state['mine'] = []
        project_coordination.assert_free(self.engine, [self.free])
        self.assertFalse((self.root / 'build/claims').exists())
        self.assertFalse((self.root / 'build/coordination/reservation-comment-proposal.txt').exists())

    def test_public_first_requires_all_linked_source_scopes(self):
        self.engine.owner_paths[self.free].add('src/linked-boot.c')
        self.state['mine'] = [(set(), {self.engine.owners[self.free]})]
        with self.assertRaises(ValueError):
            project_coordination.assert_free(self.engine, [self.free])
        self.state['mine'][0][1].add('src/linked-boot.c')
        project_coordination.assert_free(self.engine, [self.free])

    def test_local_first_still_rejects_foreign_scopes_integration_and_unselected_overlaps(self):
        self.local_first()
        for name in (self.foreign, self.overlap, self.integrated):
            for check in (project_coordination.assert_free, rac3_coordination.assert_free,
                          rac3_coordination.assert_publication_ready):
                with self.subTest(name=name, check=check.__name__), self.assertRaises(ValueError):
                    check(self.engine, [name])

    def test_local_first_start_rechecks_ticket_after_discovery_before_taking_claims(self):
        self.local_first()
        self.assertIn(self.free, {row['name'] for row in auto_jobs.plan(self.engine)})
        self.state['other'].append((set(), {self.engine.owners[self.free]}))
        manager = auto_jobs.Manager(self.root)
        manager._engine = self.engine
        with self.assertRaises(ValueError):
            manager.create([self.free])
        self.assertFalse((self.root / 'build/claims').exists())
        self.assertFalse(manager.directory.exists())

    def test_local_first_rechecks_linked_source_units_and_upstream_integration(self):
        self.local_first()
        linked = 'src/linked-boot.c'
        self.engine.owner_paths[self.free].add(linked)
        project_coordination.assert_free(self.engine, [self.free])
        self.state['other'].append((set(), {linked}))
        with self.assertRaises(ValueError):
            project_coordination.assert_free(self.engine, [self.free])
        self.state['other'].pop()
        self.state['open'].remove(self.free)
        with self.assertRaises(ValueError):
            project_coordination.assert_free(self.engine, [self.free])

    def test_publication_requires_only_selected_source_scopes_and_rechecks_later_conflicts(self):
        self.local_first()
        linked = 'src/linked-boot.c'
        self.engine.owner_paths[self.free].add(linked)
        with self.assertRaisesRegex(ValueError, 'RAC3 PR:') as missing:
            rac3_coordination.assert_publication_ready(self.engine, [self.free])
        from presentation_language import markdown
        english = markdown(str(missing.exception), 'en')
        self.assertIn('Selected functions need your own public source scope.', english)
        self.assertEqual(markdown(english, 'de'), str(missing.exception))
        proposal = (self.root / 'build/coordination/reservation-comment-proposal.txt').read_text(encoding='utf-8')
        self.assertIn(self.engine.owners[self.free], proposal)
        self.assertIn(linked, proposal)
        self.assertNotIn(self.engine.owners[self.other_free], proposal)
        self.state['mine'] = [(set(), {self.engine.owners[self.free]})]
        with self.assertRaises(ValueError):
            rac3_coordination.assert_publication_ready(self.engine, [self.free])
        self.state['mine'][0][1].add(linked)
        rac3_coordination.assert_publication_ready(self.engine, [self.free])
        self.state['other'].append((set(), {linked}))
        with self.assertRaises(ValueError):
            rac3_coordination.assert_publication_ready(self.engine, [self.free])

    def test_local_first_unavailable_coordination_and_unknown_workflow_fail_closed(self):
        self.local_first()
        with patch.object(rac3_coordination, 'snapshot', side_effect=ValueError('unavailable')):
            for check in (project_coordination.assert_free, rac3_coordination.assert_free,
                          rac3_coordination.assert_publication_ready):
                with self.subTest(check=check.__name__), self.assertRaisesRegex(ValueError, 'unavailable'):
                    check(self.engine, [self.free])
        self.engine.config['coordination']['workflow'] = 'typo'
        for discovery in (False, True):
            with self.assertRaisesRegex(ValueError, 'coordination.workflow'):
                project_coordination.blocked(self.engine, discovery=discovery)

    def matching_fixture(self):
        # Real local claim files and a synthetic compiler; no game data/network.
        from tests.test_project_engine import ProjectTests
        from project_engine import Engine
        fixture = ProjectTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        engine = Engine(fixture.root)
        engine.config.update(adapter='RAC3_PAL', coordination=dict(mode='native', workflow='local-first'))
        engine.owners = {fixture.name: 'src/synthetic.c'}
        engine.owner_paths = {fixture.name: {engine.owners[fixture.name]}}
        manager = auto_jobs.Manager(fixture.root)
        manager._engine = engine
        return fixture, engine, manager

    def test_local_first_queue_compiles_and_keeps_local_claim_until_handoff(self):
        from project_claims import ClaimAPI
        fixture, engine, manager = self.matching_fixture()
        session = manager.create([fixture.name], dict(workers=1, maxAttempts=2, seconds=15),
                                 seeds={fixture.name: fixture.source()})
        claims = ClaimAPI(fixture.root)
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
        self.assertFalse((fixture.root / 'build/coordination/reservation-comment-proposal.txt').exists())

    def test_local_first_manual_compile_and_released_retry_use_same_fresh_gate(self):
        from project_claims import ClaimAPI
        fixture, engine, manager = self.matching_fixture()
        result = engine.dispatch(dict(op='compare', name=fixture.name, source=fixture.source()))
        self.assertTrue(result['byteMatch'], result['log'])
        self.assertIsNone(ClaimAPI(fixture.root).holder(fixture.name))
        session = manager.create([fixture.name], dict(workers=1, maxAttempts=1, seconds=5),
                                 seeds={fixture.name: fixture.source(41)})
        with patch('auto_jobs.AutoEngine', return_value=engine):
            auto_jobs.search_job(manager, session['id'], fixture.name)
        manager.handoff(session['id'], fixture.name)
        self.assertIn(fixture.name, {row['name'] for row in manager.retry_plan(scope='all')})
        self.state['other'].append((set(), {engine.owners[fixture.name]}))
        with self.assertRaises(ValueError):
            manager.create_retry([fixture.name])
        with self.assertRaises(ValueError):
            engine.dispatch(dict(op='compare', name=fixture.name, source=fixture.source()))
        self.state['other'].pop()
        retry = manager.create_retry([fixture.name])
        self.assertNotEqual(retry['owner'], session['owner'])
        self.assertEqual(ClaimAPI(fixture.root).holder(fixture.name), retry['owner'])


if __name__ == '__main__':
    unittest.main()
