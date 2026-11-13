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
from RAC4_PAL import public_reservations, upstream_matches


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
        for module, name in ((public_reservations, 'fetch_reservations'),
                             (upstream_matches, 'fetch_matches')):
            with self.subTest(service=name), patch.object(module, name,
                    side_effect=ValueError('Fresh coordination unavailable')):
                with self.assertRaisesRegex(ValueError, 'Fresh coordination unavailable'):
                    self.candidates()
                with self.assertRaisesRegex(ValueError, 'Fresh coordination unavailable'):
                    project_coordination.assert_free(self.engine, [self.own])

    def test_discovery_does_not_bypass_native_mode(self):
        self.engine.config['coordination']['mode'] = 'local'
        with self.assertRaisesRegex(ValueError, 'native coordination gate'):
            self.candidates()


if __name__ == '__main__':
    unittest.main()
