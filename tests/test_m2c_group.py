"""Synthetic multi-function input, ownership and adapter routing regressions."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import m2c_group
import rough
from project_engine import Engine
from project_claims import ClaimAPI
from tests import test_project_engine as fixtures
from tests.elf_fixture import elf


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.project = fixtures.ProjectTests(methodName='runTest')
        self.project.setUp(); self.addCleanup(self.project.doCleanups)
        self.root = self.project.root
        self.first, self.second = self.project.name, 'func_00100008'
        self.names = [self.first, self.second]
        self.project.write('baserom/main.elf', elf(self.project.words * 2, executable=True))
        self.project.settings['programs']['main']['sha256'] = self.project.sha('baserom/main.elf')
        self.project.inventory = [dict(name=n, placements=[dict(program='main', section='.text',
            address=0x100000 + i * 8, size=8)]) for i, n in enumerate(self.names)]
        self.project.save()
        self.engine = Engine(self.root)
        tool = self.root / 'build/ext/m2c/m2c.py'
        tool.parent.mkdir(parents=True); tool.write_text('# synthetic process fixture\n')
        self.claims = ClaimAPI(self.root)
        self.output = '\n'.join('int ' + n + '(void) { return 42; }' for n in self.names)

    def test_one_process_selects_both_and_preserves_drafts_contracts_and_claims(self):
        draft = self.root / 'build/studio' / self.first / 'draft.c'
        draft.parent.mkdir(parents=True); draft.write_text('unsaved-user-work-on-disk')
        self.engine.contracts = {self.second: [dict(declaration='int ' + self.second + '(void);')]}
        def run(command, **kwargs):
            self.assertTrue(all(self.claims.holder(n) == self.engine.manual_owner for n in self.names))
            self.assertGreater(kwargs['timeout'], 0); self.assertLessEqual(kwargs['timeout'], 45)
            return SimpleNamespace(returncode=0, stdout=self.output, stderr='')
        with patch('local_process.run', side_effect=run) as execute:
            result = self.engine.dispatch(dict(op='decompileGroup', name=self.first, names=self.names))
        self.assertEqual(execute.call_count, 1)
        self.assertTrue(result['ok'])
        self.assertEqual([result['command'][i+1] for i, flag in enumerate(result['command']) if flag == '-f'], self.names)
        self.assertEqual(result['command'].count('--passes'), 1)
        self.assertEqual(len(result['inputs']), 2)
        self.assertEqual(result['contextDeclarations'], ['int ' + self.second + '(void);'])
        self.assertIn(self.output, Path(result['preview']).read_text())
        self.assertNotIn('source', result)
        self.assertTrue((Path(result['directory']) / 'result.json').is_file())
        self.assertEqual(draft.read_text(), 'unsaved-user-work-on-disk')
        self.assertFalse((self.root / 'build/studio' / self.second / 'draft.c').exists())
        self.assertFalse((self.root / 'build/automatic').exists())
        self.assertTrue(all(self.claims.holder(n) is None for n in self.names))

    def test_foreign_claim_fails_before_reading_bodies_or_launching(self):
        self.claims.claim('foreign-owner', self.second)
        with patch.object(self.engine, 'asm', side_effect=AssertionError('No body read')), \
                patch('local_process.run') as run:
            with self.assertRaisesRegex(ValueError, 'reserved'):
                m2c_group.decompile(self.engine, self.first, self.names)
        run.assert_not_called()
        self.assertIsNone(self.claims.holder(self.first))
        self.assertEqual(self.claims.holder(self.second), 'foreign-owner')

    def test_partial_claim_acquisition_rolls_back_on_race(self):
        original = self.engine.manual_lease
        @contextmanager
        def lease(name):
            if name == self.second:
                self.claims.claim('racing-owner', name)
            with original(name):
                yield
        with patch.object(self.engine, 'manual_lease', side_effect=lease), patch('local_process.run') as run:
            with self.assertRaisesRegex(ValueError, 'reserved'):
                m2c_group.decompile(self.engine, self.first, self.names)
        run.assert_not_called()
        self.assertIsNone(self.claims.holder(self.first))
        self.assertEqual(self.claims.holder(self.second), 'racing-owner')

    def test_changed_inputs_reject_preview_and_release_own_claims(self):
        def run(*args, **kwargs):
            (self.root / 'include/common.h').write_text('changed context')
            return SimpleNamespace(returncode=0, stdout=self.output, stderr='')
        with patch('local_process.run', side_effect=run):
            with self.assertRaisesRegex(ValueError, 'inputs changed|SHA-256 differs'):
                m2c_group.decompile(self.engine, self.first, self.names)
        self.assertTrue(all(self.claims.holder(n) is None for n in self.names))

    def test_timeout_and_partial_failure_never_become_complete_previews(self):
        for response in [subprocess.TimeoutExpired('m2c', 1),
                         SimpleNamespace(returncode=1, stdout=self.output, stderr='failed'),
                         SimpleNamespace(returncode=0, stdout='int ' + self.first + '(void) {}', stderr='')]:
            with self.subTest(response=response):
                kwargs = dict(side_effect=response) if isinstance(response, Exception) else dict(return_value=response)
                with patch('local_process.run', **kwargs):
                    result = m2c_group.decompile(self.engine, self.first, self.names)
                self.assertFalse(result['ok'])
                self.assertTrue(all(self.claims.holder(n) is None for n in self.names))

    def test_changed_assembly_snapshot_and_context_are_rejected(self):
        for kind in ['input', 'context']:
            def run(command, **kwargs):
                target = command[-1] if kind == 'input' else command[command.index('--context') + 1]
                Path(target).write_text('changed during decompilation')
                return SimpleNamespace(returncode=0, stdout=self.output, stderr='')
            with self.subTest(kind=kind), patch('local_process.run', side_effect=run):
                with self.assertRaisesRegex(ValueError, 'inputs changed'):
                    m2c_group.decompile(self.engine, self.first, self.names)
            self.assertTrue(all(self.claims.holder(n) is None for n in self.names))

    def test_invalid_duplicate_cross_program_overlap_and_oversize_selections(self):
        for names in [None, [], [self.first], [self.first, self.first], [self.second, 'unknown'], self.names * 5]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                m2c_group.selection(self.engine, self.first, names)
        original = self.engine.places[self.second]
        self.engine.places[self.second] = ('other', *original[1:])
        with self.assertRaisesRegex(ValueError, 'same program'):
            m2c_group.selection(self.engine, self.first, self.names)
        self.engine.places[self.second] = original
        self.engine.aliases[self.second] = [self.engine.place(self.first)[:4]]
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            m2c_group.selection(self.engine, self.first, self.names)
        self.engine.places[self.second] = (*original[:3], 65536, original[4])
        with self.assertRaisesRegex(ValueError, '64 KiB'):
            m2c_group.selection(self.engine, self.first, self.names)

    def test_all_placements_need_complete_boundaries_before_launch(self):
        self.project.write('baserom/main.elf', elf([0, 0] + self.project.words, executable=True))
        self.engine.config['programs']['main']['sha256'] = self.project.sha('baserom/main.elf')
        with patch('local_process.run') as run:
            with self.assertRaisesRegex(ValueError, 'function boundary'):
                m2c_group.decompile(self.engine, self.first, self.names)
        run.assert_not_called()
        self.assertTrue(all(self.claims.holder(n) is None for n in self.names))

    def test_plan_lists_known_same_program_direct_calls_without_reading_callee(self):
        listing = 'glabel ' + self.first + '\n/* 00100000 0C040002 */ jal ' + self.second + '\n'
        with patch.object(self.engine, 'asm', return_value=listing) as asm:
            rows = self.engine.dispatch(dict(op='decompileGroupPlan', name=self.first))
        self.assertEqual(rows, [dict(name=self.second, size=8)])
        asm.assert_called_once_with(self.first)
        self.assertTrue(all(self.claims.holder(n) is None for n in self.names))

    def test_missing_m2c_and_exhausted_preparation_deadline_launch_nothing(self):
        with patch('rough.tool', return_value=None), patch('local_process.run') as run:
            with self.assertRaisesRegex(ValueError, 'missing'):
                m2c_group.decompile(self.engine, self.first, self.names)
        run.assert_not_called()
        with patch('local_process.run') as run:
            with self.assertRaisesRegex(ValueError, 'time limit'):
                m2c_group.decompile(self.engine, self.first, self.names, timeout=0)
        run.assert_not_called()
        self.assertTrue(all(self.claims.holder(n) is None for n in self.names))


class RoutingTests(unittest.TestCase):
    def test_rac3_uses_its_decoder_through_shared_entrypoint_including_preparation(self):
        from RAC3_PAL.rac3_engine import Engine as Rac3
        from RAC3_PAL import division_padding
        from types import MethodType
        with tempfile.TemporaryDirectory() as folder:
            name = 'func_L01_00001000'
            engine = SimpleNamespace(root=Path(folder), contracts={},
                asm=lambda n: 'glabel ' + n + '\n/* 00001000 460C0004 */ c1 0xC0004\n',
                assert_access=lambda n, owner: None)
            engine.decompile = MethodType(Rac3.decompile, engine)
            with patch('rough.tool', return_value=Path('synthetic-m2c.py')), \
                    patch.object(division_padding, 'inspect', return_value=dict(status='not-applicable')), \
                    patch('rough.forwarding_evidence', return_value=dict(findings=[], warnings=[])), \
                    patch('local_process.run', return_value=SimpleNamespace(returncode=0,
                        stdout='float ' + name + '(float x) { return sqrtf(x); }', stderr='')) as run:
                prepared = rough.decompile(engine, name, prepare_only=True)
                run.assert_not_called()
                result = rough.decompile(engine, name, timeout=17)
            self.assertEqual(result['instructionDecodings'][0]['word'], '460C0004')
            self.assertEqual(run.call_args.kwargs['timeout'], 17)
            self.assertIn('sqrt.s $f0, $f12', (Path(prepared['directory']) / 'input.s').read_text())

    def test_rac4_uses_shared_m2c_with_its_own_engine_services(self):
        from RAC4_PAL.engine import AutoEngine as Rac4
        engine = object.__new__(Rac4)
        with patch('rough._decompile', return_value={'shared': True}) as run:
            self.assertEqual(rough.decompile(engine, 'func_00100000', owner='owner', timeout=19), {'shared': True})
        run.assert_called_once_with(engine, 'func_00100000', 'owner', 19)


@unittest.skipUnless(os.environ.get('PS2DECOMP_M2C'), 'Optional local upstream m2c integration')
class UpstreamTests(unittest.TestCase):
    def test_real_m2c_changes_helper_type_only_when_both_functions_selected(self):
        script = Path(os.environ['PS2DECOMP_M2C']).resolve()
        caller, callee = 'func_00100000', 'func_00100100'
        assembly = ('.text\nglabel ' + caller + '\naddiu $sp, $sp, -0x10\nsw $ra, 0xc($sp)\n'
            'addiu $a0, $a0, 4\njal ' + callee + '\nnop\nadd.s $f0, $f0, $f0\n'
            'lw $ra, 0xc($sp)\njr $ra\naddiu $sp, $sp, 0x10\nglabel ' + callee +
            '\nlwc1 $f0, 0($a0)\njr $ra\nnop\n')
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'input.s'; source.write_text(assembly)
            base = [__import__('sys').executable, str(script), '-t', 'mipsee-gcc-c', '--valid-syntax', '--no-cache', '-f', caller]
            single = subprocess.run([*base, str(source)], capture_output=True, text=True, timeout=15, check=True).stdout
            group = subprocess.run([*base, '-f', callee, str(source)], capture_output=True, text=True, timeout=15, check=True).stdout
        self.assertIn(callee + '(s32)', single)
        self.assertIn(callee + '(f32 *arg0)', group)
        self.assertIn(caller + '(s32 arg0)', group)  # Not proof of caller pointer ABI.
