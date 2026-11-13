"""Indexed reservation checks must preserve the exhaustive overlap decisions."""
from pathlib import Path
from types import SimpleNamespace
import random
import tempfile
import unittest

from auto_jobs import PlacementIndex, eligible


class ReservationIntervals(unittest.TestCase):
    def test_nested_equal_adjacent_and_all_overlay_intervals_against_exhaustive_check(self):
        rng = random.Random(781)
        places = {}
        for i in range(100):
            places[f'claim{i}'] = [(rng.choice(['main', 'level_00', 'level_01']),
                                    rng.choice(['.text', '.data']), rng.randrange(400),
                                    rng.randrange(1, 150)) for _ in range(rng.randrange(1, 5))]
        places['long'] = [('main', '.text', 0, 1000)]
        canonical = [{'places': [('level_02', '.text', 100, 20)]}]
        engine = SimpleNamespace(places=places, all_places=lambda name: places[name])
        inventory = ({'mapped': canonical[0]}, set(), set(), {n: 'same-owner' for n in places})
        index = PlacementIndex(engine, inventory)
        for i in range(1000):
            name = rng.choice(list(places) + ['free'])
            wanted = [(rng.choice(['main', 'level_00', 'level_01', 'level_02', 'absent']),
                       rng.choice(['.text', '.data']), rng.randrange(500), rng.randrange(1, 80))]
            all_rows = [(p, None) for row in canonical for p in row['places']]
            all_rows += [(p, n) for n, ps in places.items() if n != name for p in ps]
            expected = any(p[0] == q[0] and p[1] == q[1] and p[2] < q[2] + q[3]
                           and q[2] < p[2] + p[3] for p in wanted for q, _ in all_rows)
            self.assertEqual(index.overlaps(wanted, name), expected, (name, wanted))
        only = SimpleNamespace(places={'self': [('main', '.text', 10, 10)]},
                               all_places=lambda n: [('main', '.text', 10, 10)])
        one = PlacementIndex(only, ({}, set(), set(), {'self': 'owner'}))
        self.assertFalse(one.overlaps([('main', '.text', 20, 4)], 'other'))
        self.assertFalse(one.overlaps([('main', '.text', 6, 4)], 'other'))
        self.assertFalse(one.overlaps([('main', '.text', 10, 10)], 'self'))
        self.assertTrue(one.overlaps([('main', '.text', 19, 4)], 'other'))
        self.assertTrue(index.overlaps([('main', '.text', 999, 4)], 'free'))

    def test_eligible_matches_original_and_blocks_same_owner_alias_and_foreign_self(self):
        with tempfile.TemporaryDirectory() as root:
            p = {'wanted': ('main', '.text', 100, 8, 'main'),
                 'alias': ('level_00', '.text', 208, 8, 'level')}
            aliases = {'wanted': [('main', '.text', 100, 8), ('level_00', '.text', 200, 16)],
                       'alias': [p['alias'][:4]]}
            engine = SimpleNamespace(root=Path(root), places=p, remnants=set(),
                                     place=lambda n: p[n], all_places=lambda n: aliases[n])
            for canonical in [{}, {'mapped': {'places': [('level_00', '.text', 204, 4)]}}]:
                for holders in [{}, {'wanted': 'me'}, {'wanted': 'other'},
                                {'wanted': 'me', 'alias': 'me'}, {'alias': 'other'}, {'unknown': 'other'}]:
                    inventory = (canonical, set(), set(), holders)
                    index = PlacementIndex(engine, inventory)
                    expected = eligible(engine, 'wanted', 'me', inventory=inventory, finals=set(), history=set())
                    self.assertEqual(eligible(engine, 'wanted', 'me', inventory=inventory, finals=set(),
                                              history=set(), placement_index=index), expected)
            fresh = PlacementIndex(engine, ({}, set(), set(), {'alias': 'me'}))
            self.assertTrue(fresh.overlaps(aliases['wanted'], 'wanted'))

    def test_batch_queries_do_not_reenumerate_every_claim_and_refresh_observes_new_claim(self):
        calls = []
        places = {f'f{i}': [('main', '.text', i * 16, 8)] for i in range(1000)}
        def all_places(name):
            calls.append(name)
            return places[name]
        engine = SimpleNamespace(places=places, all_places=all_places)
        inventory = ({}, set(), set(), {name: 'owner' for name in places})
        index = PlacementIndex(engine, inventory)
        self.assertEqual(len(calls), 1000)
        for name in places:
            self.assertFalse(index.overlaps(places[name], name))
        self.assertEqual(len(calls), 1000)
        places['new_alias'] = places['f3']
        inventory[3]['new_alias'] = 'owner'
        refreshed = PlacementIndex(engine, inventory)
        self.assertTrue(refreshed.overlaps(places['f3'], 'f3'))


if __name__ == '__main__':
    unittest.main()
