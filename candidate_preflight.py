"""Conservative R5900 candidate screening; never a match or ABI certificate.

No catalogue addresses or compiler changes: inspect the actual instruction
window. Unknown/dynamic stack restoration is held for read-only manual review.
Calls are assumed to obey EABI (preserve SP/FP). Branch delay instructions are
executed on both edges, except the annulled not-taken edge of branch-likely.
"""
from __future__ import annotations

from collections import deque
import struct


MAX_WORDS = 8192
MAX_STATES = 16384
MAX_FRAME = 1024 * 1024


def inspect(raw: bytes, start: int) -> dict:
    result = dict(eligible=False, reasons=[], warnings=[], evidence=[])
    reported = set()

    def note(code, offset, detail, reject=True):
        key = code, offset, detail
        if key in reported:
            return
        reported.add(key)
        row = dict(code=code, offset=offset, detail=detail)
        if offset is not None:
            row['address'] = f'{start + offset:08X}'
        result['evidence'].append(row)
        target = result['reasons'] if reject else result['warnings']
        if detail not in target:
            target.append(detail)

    if not isinstance(start, int) or start < 0 or start % 4:
        note('invalid_start', None, 'Ungültige oder nicht ausgerichtete Startadresse.')
        return result
    if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) % 4:
        note('invalid_window', None, 'Funktionsfenster fehlt oder enthält unvollständige Instruktionen.')
        return result
    if len(raw) // 4 > MAX_WORDS:
        note('analysis_limit', None, 'Funktionsfenster überschreitet das statische Prüflimit.')
        return result
    words = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
    pending = deque([(0, 0, None)])  # PC, SP relative to entry, known FP relative to entry
    seen, executed, returns = set(), set(), set()
    heights = {}
    calls = set()

    def fields(w):
        return w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, w & 63

    def immediate(w):
        return (w & 0x7fff) - (w & 0x8000)

    def control(w):
        op, rs, rt, rd, fn = fields(w)
        return (op in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23)
                or op == 0 and fn in (8, 9, 12, 13, 48, 49, 50, 51, 52, 54)
                or op == 16 or op in (17, 18) and rs == 8)

    def step(offset, sp, fp):
        """Apply one non-control instruction's known SP/FP effects."""
        executed.add(offset)
        w = words[offset // 4]
        op, rs, rt, rd, fn = fields(w)
        if op in (8, 24) or op == 0 and fn in (32, 34, 44, 46):
            note('trapping_arithmetic', offset,
                 'Trappende ADD/ADDI/SUB/DADD/DSUB-Arithmetik: kein unterstütztes Plain-C-Suchmuster.')
        if op == 18 or op in (50, 54, 58, 62):
            note('vu_cop2', offset,
                 'VU/COP2-Instruktion: benötigt unterstützte Intrinsics oder manuelles Assembly-Review.')
        if op == 16:
            note('privileged_control', offset, 'COP0/privilegierte Instruktion benötigt manuelles Review.')
        if op == 0 and fn in (12, 13, 48, 49, 50, 51, 52, 54):
            note('trap_or_syscall', offset, 'Trap/Syscall/Break benötigt manuelles Review.')

        destination, alias = None, None
        source = sp if rs == 29 else fp if rs == 30 else None
        if op in (8, 9, 24, 25):
            destination = rt
            alias = source + immediate(w) if source is not None else None
        elif op in (10, 11, 12, 13, 14, 15, 26, 27, 30, 32, 33, 34, 35,
                    36, 37, 38, 39, 48, 52, 55, 56, 60):
            destination = rt
        elif op == 0:
            if fn not in (8, 12, 13, 15, 17, 19, 48, 49, 50, 51, 52, 54):
                destination = rd
            if fn in (33, 37, 45) and rt == 0:
                alias = source
            elif fn in (33, 37, 45) and rs == 0:
                alias = sp if rt == 29 else fp if rt == 30 else None
        elif op == 28:  # R5900 MMI general-register destination
            destination = rd
        elif op in (17, 18) and rs in (0, 1, 2):
            destination = rt
        elif op == 3:
            destination = 31
        elif op not in (1, 2, 4, 5, 6, 7, 16, 17, 18, 20, 21, 22, 23,
                        31, 40, 41, 42, 43, 44, 45, 46, 47, 49, 50, 51,
                        53, 54, 57, 58, 61, 62, 63):
            # Unknown formats must not silently write either frame register.
            if rt in (29, 30) or rd in (29, 30):
                note('unknown_frame_write', offset,
                     'Unbekanntes Instruktionsformat könnte SP/FP überschreiben.')

        if destination == 29:
            if alias is None:
                note('unknown_sp_write', offset,
                     'SP wird aus einem nicht belegten Wert wiederhergestellt oder dynamisch geändert.')
                return None
            sp = alias
            if sp > 0:
                note('stack_above_entry', offset,
                     'Stack wird über den Eintrittswert abgebaut: Prolog/Funktionsgrenze fehlt vermutlich.')
                return None
            if abs(sp) > MAX_FRAME:
                note('stack_limit', offset, 'Stackhöhe überschreitet das statische Prüflimit.')
                return None
        elif destination == 30:
            fp = alias if alias is None or abs(alias) <= MAX_FRAME else None
        return sp, fp

    def enqueue(offset, state, origin):
        if state is None:
            return
        if offset % 4 or not 0 <= offset < len(raw):
            note('outside_window', origin,
                 'Erreichbarer Kontrollfluss verlässt das Funktionsfenster ohne gültige Rückkehr.')
            return
        item = (offset, *state)
        if item not in seen:
            pending.append(item)

    def delay(pc, sp, fp):
        if pc + 4 >= len(raw):
            note('missing_delay', pc, 'Kontrollinstruktion hat keinen vollständigen Delay-Slot.')
            return None
        if control(words[pc // 4 + 1]):
            note('control_in_delay', pc + 4, 'Kontrollinstruktion im Delay-Slot ist nicht unterstützt.')
            return None
        return step(pc + 4, sp, fp)

    while pending:
        pc, sp, fp = pending.popleft()
        state = pc, sp, fp
        if state in seen:
            continue
        if len(seen) >= MAX_STATES:
            note('analysis_limit', pc, 'Zu viele CFG-/Stackzustände; manuelles Review erforderlich.')
            break
        prior = heights.setdefault(pc, set())
        prior.add(sp)
        if len(prior) > 32:
            note('stack_cycle', pc, 'Wiederholter CFG-Pfad ändert die Stackhöhe; Prüfung begrenzt.')
            break
        seen.add(state)
        executed.add(pc)
        w = words[pc // 4]
        op, rs, rt, rd, fn = fields(w)

        if op == 1 and rt not in (0, 1, 2, 3):
            note('unsupported_regimm', pc, 'REGIMM-Trap/bedingter Link benötigt manuelles Review.')
            continue
        branch = op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op == 17 and rs == 8
        call = op == 3 or op == 0 and fn == 9
        ret = op == 0 and fn == 8
        if op == 2:
            target = ((start + pc + 4) & 0xf0000000) | ((w & 0x3ffffff) << 2)
            if not start <= target < start + len(raw):
                note('tail_call', pc, 'Direkter Tail-Call außerhalb des Fensters hat kein unterstütztes C-Profil.')
                continue
            enqueue(target - start, delay(pc, sp, fp), pc)
        elif ret:
            if rs != 31:
                note('indirect_jump', pc, 'Indirekter Sprung ist keine belegte Funktionsrückkehr.')
                continue
            after = delay(pc, sp, fp)
            if after is not None:
                if after[0] != 0:
                    note('unbalanced_return', pc, f'Rückkehr mit unausgeglichenem Stack ({after[0]} Byte).')
                else:
                    returns.add(pc)
        elif call:
            if op == 0 and rd != 31:
                note('unsupported_link_register', pc, 'JALR nutzt ein nicht unterstütztes Link-Register.')
                continue
            calls.add(pc)
            enqueue(pc + 8, delay(pc, sp, fp), pc)
        elif branch:
            likely = op in (20, 21, 22, 23) or op == 1 and rt in (2, 3) or op == 17 and bool(rt & 2)
            known = None
            if op in (4, 20) and rs == rt or op in (6, 22) and rs == 0 or op == 1 and rs == 0 and rt in (1, 3):
                known = True
            if op in (5, 21) and rs == rt or op in (7, 23) and rs == 0 or op == 1 and rs == 0 and rt in (0, 2):
                known = False
            # Even an annulled slot must be a valid physical instruction slot.
            if pc + 4 >= len(raw) or control(words[pc // 4 + 1]):
                delay(pc, sp, fp)
                continue
            # A statically annulled delay slot still belongs to this window;
            # its bytes are not evidence of a second, unreachable function.
            executed.add(pc + 4)
            after = delay(pc, sp, fp) if known is not False or not likely else (sp, fp)
            if known is not False:
                enqueue(pc + 4 + immediate(w) * 4, after, pc)
            if known is not True:
                enqueue(pc + 8, (sp, fp) if likely else after, pc)
        elif control(w):
            step(pc, sp, fp)
            note('unsupported_control', pc, 'Nicht unterstützte Kontrollinstruktion benötigt Review.')
        else:
            enqueue(pc + 4, step(pc, sp, fp), pc)

    if not returns:
        note('no_balanced_return', None, 'Keine erreichbare Rückkehr mit belegter Stackbalance.')
    unreachable = [i * 4 for i, w in enumerate(words) if w and i * 4 not in executed]
    if unreachable:
        note('unreachable_code', unreachable[0],
             'Nicht erreichbare Nicht-NOP-Instruktionen deuten auf falsche Grenzen oder Codefragmente.')
    if calls:
        note('eabi_call_assumption', None,
             'Stackprüfung setzt voraus, dass aufgerufene Funktionen SP/FP gemäß EABI erhalten.', reject=False)
    result['evidence'].append(dict(code='summary', states=len(seen),
                                   balancedReturns=len(returns), calls=len(calls),
                                   inspectedBytes=len(raw)))
    result['eligible'] = not result['reasons']
    return result
