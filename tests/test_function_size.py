"""Exercise range selection against synthetic functions, never original game data."""
import unittest

import auto_jobs
import candidates
import function_size
from project_engine import Engine
from tests.elf_fixture import elf
from tests import test_project_engine as fixtures


class FunctionSizeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProjectTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.name = self.fixture.name

    def engine(self, size):
        # Straight-line nops ending in a return and its delay slot.
        words = [0] * (size // 4 - 2) + [0x03e00008, 0]
        self.fixture.write('baserom/main.elf', elf(words, executable=True))
        self.fixture.settings['programs']['main']['sha256'] = self.fixture.sha('baserom/main.elf')
        self.fixture.inventory[0]['placements'][0]['size'] = size
        self.fixture.save()
        return Engine(self.root)

    def manager(self, size):
        manager = auto_jobs.Manager(self.root)
        manager._engine = self.engine(size)
        return manager

    def test_defaults_and_inclusive_bounds(self):
        self.assertEqual(function_size.settings(), dict(minBytes=8, maxBytes=640))
        for size, included in ((252, False), (256, True), (640, True), (644, False)):
            self.assertEqual(function_size.contains(size, dict(minBytes=256, maxBytes=640)), included)
        self.assertFalse(function_size.contains(4, dict(minBytes=0, maxBytes=8)))
        self.assertTrue(function_size.contains(32768, dict(maxBytes=32768)))
        # Old saved options gain defaults without modifying the original session.
        legacy = dict(workers=1, maxAttempts=12, seconds=50)
        resolved = auto_jobs.options(legacy)
        self.assertNotIn('minBytes', legacy)
        self.assertEqual((resolved['minBytes'], resolved['maxBytes']), (8, 640))
        self.assertEqual(resolved['maxAttempts'], 12)

    def test_invalid_ranges_fail_closed(self):
        for value in ([], '', False, dict(minBytes=True), dict(maxBytes=640.0),
                      dict(minBytes=-1), dict(maxBytes=7), dict(minBytes=641, maxBytes=640),
                      dict(maxBytes=32769), dict(maxBytes=None)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                auto_jobs.options(value)

    def test_new_discovery_and_manual_picker_use_the_range_without_claiming(self):
        engine = self.engine(644)
        self.assertEqual(auto_jobs.plan(engine), [])
        self.assertFalse(candidates.boundary(engine, self.name))
        limits = dict(minBytes=644, maxBytes=644)
        self.assertEqual([r['name'] for r in auto_jobs.plan(engine, settings=limits)], [self.name])
        engine.function_size_range = limits
        self.assertTrue(candidates.boundary(engine, self.name))
        self.assertEqual(candidates.plan(engine), [self.name])
        self.assertEqual(auto_jobs.plan(engine, settings=dict(minBytes=645, maxBytes=1000)), [])
        self.assertFalse((self.root / 'build/claims').exists())
        self.assertFalse((self.root / 'build/autodecomp').exists())

    def test_start_rejects_outside_range_before_claim_and_persists_valid_range(self):
        manager = self.manager(644)
        for create in (manager.create, manager.create_retry):
            with self.assertRaisesRegex(ValueError, 'Byte-Bereich'):
                create([self.name])
            self.assertIsNone(auto_jobs.claim_api(manager.engine).holder(self.name))
            self.assertFalse(manager.directory.exists())
        limits = dict(minBytes=644, maxBytes=644)
        result = manager.create([self.name], limits)
        self.assertEqual({key: result['options'][key] for key in limits}, limits)
        manager.handoff(result['id'], self.name)

    def test_released_retry_filter_and_start_keep_original_history(self):
        manager = self.manager(644)
        limits = dict(minBytes=644, maxBytes=1000)
        original = manager.create([self.name], limits)
        manager.handoff(original['id'], self.name)
        paths = list(manager.folder(original['id']).rglob('*'))
        before = {p: p.read_bytes() for p in paths if p.is_file()}
        self.assertEqual(manager.retry_plan(scope='all'), [])
        rows = manager.retry_plan(scope='all', settings=limits)
        self.assertEqual([r['name'] for r in rows], [self.name])
        retried = manager.create_retry([self.name], limits)
        self.assertEqual(retried['options']['minBytes'], 644)
        self.assertEqual(retried['options']['maxBytes'], 1000)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        manager.handoff(retried['id'], self.name)


if __name__ == '__main__':
    unittest.main()
