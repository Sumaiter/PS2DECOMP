"""Candidate discovery must not grant permission to claim or compile work."""
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
        adapter = SimpleNamespace(coordination_state=lambda engine: (set(), {self.free}))
        with patch.dict(sys.modules, {'Synthetic_PAL': adapter}):
            for discovery in (False, True):
                self.assertEqual(project_coordination.blocked(self.engine, discovery=discovery),
                                 set(self.engine.places) - {self.free})

    def test_generic_command_allowlist_is_not_relaxed_for_discovery(self):
        self.engine.config = {'adapter': 'generic', 'coordination': {'mode': 'command', 'command': ['synthetic']}}
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


if __name__ == '__main__':
    unittest.main()
