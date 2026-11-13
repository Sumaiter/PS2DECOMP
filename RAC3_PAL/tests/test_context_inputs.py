"""Synthetic native input/forwarding and immutable context counterexamples."""
import hashlib
import io
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from elftools.elf.elffile import ELFFile
from tests.elf_fixture import elf
from RAC3_PAL import context_inputs as inputs
from RAC3_PAL.rac3_engine import Engine as AdapterEngine
import rough


ROOT, MIDDLE, LEAF, LAST = [f'func_L01_{address:08X}' for address in (0x100010, 0x100080, 0x100100, 0x100180)]
RET = 0x03e00008


def raw(*words):
    return struct.pack('<' + 'I' * len(words), *words)


def mem(op, base, value=1, offset=0):
    return op << 26 | base << 21 | value << 16 | (offset & 0xffff)


def move(destination, source):
    return source << 21 | destination << 11 | 45  # DADDU destination,source,zero


def movs(destination, source):
    return 0x46000006 | source << 11 | destination << 6


def call(address, delay=0):
    return raw(3 << 26 | address >> 2, delay, RET, 0)


class Engine:
    ELFFile = ELFFile
    remnants = set()

    def __init__(self, root, functions, copies=2):
        self.root, self.functions = Path(root), functions
        self.paths, self.digests, self.aliases, self.places, self._symbols = {}, {}, {}, {}, {}
        for index in range(copies):
            program = f'level_{index + 1:02d}'
            data = bytearray(0x200)
            for name, body in functions.items():
                address = int(name[-8:], 16)
                data[address - 0x100000:address - 0x100000 + len(body)] = body
                self.aliases.setdefault(name, []).append((program, '.text', address, len(body)))
                self._symbols.setdefault(program, {})[address] = name
            path = self.root / (program + '.elf')
            path.write_bytes(elf(list(struct.unpack('<' + 'I' * (len(data) // 4), data)), executable=True))
            self.paths[program] = path
            self.digests[program] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.places = {name: (*places[0], 'function') for name, places in self.aliases.items()}

    def all_places(self, name):
        return self.aliases[name]

    def place(self, name):
        return self.places[name]

    def assert_reference(self, program):
        pass

    def reference_identity(self, place):
        return dict(path=self.paths[place[0]], algorithm='sha256', digest=self.digests[place[0]],
                    eeSections=['.text'], eeSectionsEvidence='Synthetic EE instruction fixture.')

    def disc_bytes(self, place):
        sec = ELFFile(io.BytesIO(self.paths[place[0]].read_bytes())).get_section_by_name(place[1])
        offset = place[2] - sec['sh_addr']
        return sec.data()[offset:offset + place[3]]


class ContextInputsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def slots(self, uses):
        return {(use['bank'], use['register']) for use in uses}

    def test_vu_memory_base_reads_are_observed_before_unsupported_vector_math(self):
        uses = inputs.prefix_inputs(raw(mem(54, 5), mem(54, 6), 0x4be509bc, mem(62, 4), RET, 0), 0x1000)
        self.assertEqual(self.slots(uses), {('gpr', 5), ('gpr', 6)})
        self.assertEqual([use['readOffset'] for use in uses], [0, 4])
        self.assertTrue(all(use['callPath'] == [] for use in uses))

    def test_gpr_clobber_zeroing_moves_and_unknown_instructions_cannot_fabricate_inputs(self):
        self.assertEqual(inputs.prefix_inputs(raw(move(5, 0), mem(54, 5), RET, 0), 0x1000), [])
        self.assertEqual(inputs.prefix_inputs(raw(5 << 21 | 5 << 16 | 5 << 11 | 38, mem(54, 5)), 0x1000), [])
        uses = inputs.prefix_inputs(raw(move(5, 6), mem(54, 5), RET, 0), 0x1000)
        self.assertEqual(self.slots(uses), {('gpr', 6)})
        for raw_bytes in (raw(0x70000000, mem(54, 5)), raw(move(5, 6) | 1 << 6, mem(54, 5)), b'', b'\x00'):
            self.assertEqual(inputs.prefix_inputs(raw_bytes, 0x1000), [])

    def test_no_reads_are_carried_through_branches_returns_or_indirect_calls(self):
        for control in (0x10000001, 0x14400001, 0x54400001, 0x45030001, RET, 0x0320f809):
            self.assertEqual(inputs.prefix_inputs(raw(control, mem(54, 5), mem(54, 6)), 0x1000), [])

    def test_fp_forwarding_uses_delay_slot_state_and_never_continues_past_first_call(self):
        leaf = [dict(bank='fpr', register=12, readRegister=['fpr', 12], readOffset=0, callPath=[]),
                dict(bank='fpr', register=13, readRegister=['fpr', 13], readOffset=4, callPath=[])]
        resolve = lambda address, depth: leaf if address == 0x2000 else []
        uses = inputs.prefix_inputs(call(0x2000), 0x1000, resolve)
        self.assertEqual(self.slots(uses), {('fpr', 12), ('fpr', 13)})
        self.assertTrue(all(use['callPath'][0]['target'] == 0x2000 for use in uses))
        mtc1 = 0x44800000 | 2 << 16 | 12 << 11
        self.assertEqual(self.slots(inputs.prefix_inputs(call(0x2000, mtc1), 0x1000, resolve)), {('fpr', 13)})
        moved = inputs.prefix_inputs(call(0x2000, movs(12, 14)), 0x1000, resolve)
        self.assertEqual(self.slots(moved), {('fpr', 13), ('fpr', 14)})
        self.assertEqual(inputs.prefix_inputs(call(0x3000) + raw(movs(0, 12)), 0x1000, resolve), [])
        self.assertEqual(inputs.prefix_inputs(call(0x2000, RET), 0x1000, resolve), [])
        # A return/scratch FP value copied into f12 is not an incoming f12.
        self.assertEqual(self.slots(inputs.prefix_inputs(raw(movs(12, 0)) + call(0x2000), 0x1000, resolve)), {('fpr', 13)})

    def test_real_synthetic_elf_all_copy_hash_cfg_and_boundary_checks_prove_zero_arity_gap(self):
        engine = Engine(self.root, {ROOT: raw(mem(54, 5), mem(54, 6), RET, 0)})
        result = inputs.inspect(engine, ['void ' + ROOT + '(void);'])
        self.assertEqual(len(result['findings']), 1)
        finding = result['findings'][0]
        self.assertEqual([(s['bank'], s['register']) for s in finding['observedIncomingRegisters']], [('gpr', 5), ('gpr', 6)])
        self.assertEqual(len(finding['placements']), 2)
        self.assertTrue(all(len(row['referenceSha256']) == 64 for row in finding['placements']))
        self.assertEqual(result['unavailable'], [])
        self.assertFalse(result['mutatesInputs'])
        self.assertFalse(result['semanticApproval'])

    def test_one_pointer_context_reports_two_level_native_fp_forwarding_without_types(self):
        engine = Engine(self.root, {ROOT: call(0x100080), MIDDLE: call(0x100100),
                                   LEAF: raw(movs(20, 12), movs(21, 13), RET, 0)})
        finding = inputs.inspect(engine, ['void ' + ROOT + '(f32 *out);'])['findings'][0]
        self.assertEqual(finding['observedIncomingRegisters'], [dict(bank='fpr', register=12), dict(bank='fpr', register=13)])
        for row in finding['placements']:
            for use in row['uses']:
                self.assertEqual(len(use['callPath']), 2)
                self.assertEqual([p['callee']['name'] for p in use['callPath']], [MIDDLE, LEAF])
                self.assertTrue(all(len(p['callee']['bodySha256']) == 64 for p in use['callPath']))

    def test_third_forward_call_unknown_target_or_reassigned_fp_input_suppresses_proof(self):
        engine = Engine(self.root, {ROOT: call(0x100080), MIDDLE: call(0x100100),
                                   LEAF: call(0x100180), LAST: raw(movs(20, 12), RET, 0)})
        self.assertEqual(inputs.inspect(engine, ['void ' + ROOT + '(f32 *out);'])['findings'], [])
        engine = Engine(self.root, {ROOT: call(0x100080), MIDDLE: raw(movs(20, 12), RET, 0)})
        del engine._symbols['level_01'][0x100080]
        self.assertEqual(inputs.inspect(engine, ['void ' + ROOT + '(f32 *out);'])['findings'], [])
        engine = Engine(self.root, {ROOT: raw(0x44826000) + call(0x100080), MIDDLE: raw(movs(20, 12), RET, 0)})
        self.assertEqual(inputs.inspect(engine, ['void ' + ROOT + '(f32 *out);'])['findings'], [])

    def test_nonzero_gpr_formals_floating_formals_varargs_and_custom_types_are_not_mapped_to_slots(self):
        engine = Engine(self.root, {ROOT: raw(mem(54, 5), RET, 0)})
        self.assertEqual(inputs.inspect(engine, ['void ' + ROOT + '(void *out);'])['findings'], [])
        engine = Engine(self.root, {ROOT: raw(movs(20, 12), RET, 0)})
        for params in ('f32 scale', 'double scale', 'f32 *out, f32 scale', 's32 x, ...', 'Custom x'):
            self.assertEqual(inputs.inspect(engine, ['void ' + ROOT + '(' + params + ');'])['findings'], [])

    def test_missing_copy_duplicate_copy_changed_hash_remnant_and_incomplete_cfg_never_supply_positive_proof(self):
        for mode in ('missing', 'duplicate', 'hash', 'remnant', 'cfg'):
            engine = Engine(self.root, {ROOT: raw(mem(54, 5), RET, 0)})
            if mode == 'missing':
                engine.all_places = lambda name: engine.aliases[name][:1]
            elif mode == 'duplicate':
                engine.all_places = lambda name: engine.aliases[name] * 2
            elif mode == 'hash':
                engine.digests['level_02'] = '0' * 64
            elif mode == 'remnant':
                engine.remnants = {ROOT}
            else:
                engine = Engine(self.root, {ROOT: raw(mem(54, 5), 0x10000040, 0)})
            result = inputs.inspect(engine, ['void ' + ROOT + '(void);'])
            self.assertEqual(result['findings'], [], mode)
            self.assertTrue(result['unavailable'], mode)

    def test_external_interior_entry_rejects_proof_while_entry_call_is_allowed(self):
        for word, finding in ((0x14000004 | 4 << 21, False), (3 << 26 | 0x100010 >> 2, True)):
            engine = Engine(self.root, {ROOT: raw(mem(54, 5), RET, 0)})
            for program, path in engine.paths.items():
                # Rebuild an ELF with the external edge at the start of .text.
                body = bytearray(0x200)
                body[0:4] = raw(word)
                body[16:28] = raw(mem(54, 5), RET, 0)
                path.write_bytes(elf(list(struct.unpack('<128I', body)), executable=True))
                engine.digests[program] = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(bool(inputs.inspect(engine, ['void ' + ROOT + '(void);'])['findings']), finding)

    def test_secondary_only_different_input_does_not_become_a_primary_only_finding(self):
        engine = Engine(self.root, {ROOT: raw(mem(54, 5), RET, 0)})
        path = engine.paths['level_02']
        data = bytearray(0x200)
        data[16:28] = raw(mem(54, 6), RET, 0)
        path.write_bytes(elf(list(struct.unpack('<128I', data)), executable=True))
        engine.digests['level_02'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(inputs.inspect(engine, ['void ' + ROOT + '(void);'])['findings'], [])

    def test_forwarded_leaf_hash_and_interior_target_must_be_proved_independently(self):
        engine = Engine(self.root, {ROOT: call(0x100100), LEAF: raw(movs(20, 12), RET, 0)})
        original = engine.reference_identity
        def identity(place):
            record = original(place)
            if place[2] == 0x100100:
                record['digest'] = '0' * 64
            return record
        engine.reference_identity = identity
        result = inputs.inspect(engine, ['void ' + ROOT + '(f32 *out);'])
        self.assertEqual(result['findings'], [])
        self.assertTrue(result['unavailable'])
        engine = Engine(self.root, {ROOT: call(0x100104), LEAF: raw(0, movs(20, 12), RET, 0)})
        self.assertEqual(inputs.inspect(engine, ['void ' + ROOT + '(f32 *out);'])['findings'], [])

    def test_primary_applicability_bound_and_languages_do_not_claim_complete_abi(self):
        engine = Engine(self.root, {ROOT: raw(RET, 0)})
        with patch.object(inputs._Evidence, 'verified', side_effect=AssertionError('Unneeded full scan')):
            result = inputs.inspect(engine, ['void ' + ROOT + '(void);'])
        self.assertEqual(result['primaryOnlySkipped'], [ROOT])
        result = inputs.inspect(engine, ['void ' + ROOT + '(void);'] * 17)
        self.assertIn('bound', result['unavailable'][0]['reason'])
        evidence = dict(findings=[dict(name=ROOT)])
        self.assertIn('native instructions', inputs.warnings(evidence, 'en')[0])
        self.assertIn('Originalinstruktionen', inputs.warnings(evidence, 'de-DE')[0])
        self.assertEqual(inputs.warnings(dict(findings=[])), [])

    def test_decompile_surfaces_evidence_without_changing_source_context_timeout_or_abi_gate(self):
        engine = SimpleNamespace(root=self.root, contracts={LEAF: [dict(declaration='void ' + LEAF + '(void);')]},
                                 assert_access=lambda name, owner: None,
                                 asm=lambda name: 'glabel ' + name + '\n/* 00100010 0C040040 */  jal ' + LEAF + '\n')
        evidence = dict(findings=[dict(name=LEAF)], unavailable=[])
        original_flag = getattr(subprocess, 'CREATE_NO_WINDOW', None)
        if hasattr(subprocess, 'CREATE_NO_WINDOW'):
            del subprocess.CREATE_NO_WINDOW
        self.addCleanup(lambda: setattr(subprocess, 'CREATE_NO_WINDOW', original_flag)
                        if original_flag is not None else None)
        output = 'void ' + ROOT + '(void) { ' + LEAF + '(); }\n'
        with patch.object(inputs, 'inspect', return_value=evidence), \
                patch('rough.tool', return_value=Path('synthetic-m2c.py')), \
                patch('rough.forwarding_evidence', return_value=dict(findings=[], warnings=[])), \
                patch('local_process.run', return_value=SimpleNamespace(returncode=0, stdout=output, stderr='')) as run, \
                patch.dict(os.environ, PS2DECOMP_LANGUAGE='en'):
            result = AdapterEngine.decompile(engine, ROOT)
        folder = Path(result['directory'])
        self.assertEqual(result['contextInputs'], evidence)
        self.assertEqual(result['abiProblems'], [])
        self.assertEqual(folder.joinpath('input.s').read_text(), engine.asm(ROOT))
        self.assertEqual(folder.joinpath('context.c').read_text(), rough.TYPES + 'void ' + LEAF + '(void);\n')
        self.assertEqual(result['source'], '#include "common.h"\n\n/* Unverified automatic m2c draft; review types, ABI and ownership. */\nvoid ' + LEAF + '(void);\n' + output)
        self.assertEqual(run.call_args.kwargs['timeout'], 45)
        self.assertNotIn('creationflags', run.call_args.kwargs)
        self.assertTrue(any('native instructions' in warning for warning in result['warnings']))


if __name__ == '__main__':
    unittest.main()
