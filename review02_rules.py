"""Evidence-only EE argument-flow analysis, independent of candidate mutation.

An omitted C argument is not harmless when the real callee consumes the
incoming register. Results identify unchanged incoming inputs at direct calls,
after executing the delay slot. They do not invent types or rewrite prototypes.
Unknown instructions/control flow, caller-clobbered state and narrowed 128-bit
values suppress suggestions rather than guessing. No retail addresses occur in
the rules. The caller must additionally reconcile its proven C signature.
"""
from __future__ import annotations
from collections import deque
import struct

EE_ARGS = tuple(range(4, 12))
MAX_STATES = 4096
MAX_WORDS = 8192


def _parts(w):
    return w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, w & 63


def _imm(w):
    return (w & 0x7fff) - (w & 0x8000)


def _effects(w):
    """(register reads with bit widths, writes, identity copy) or unsupported."""
    op, rs, rt, rd, fn = _parts(w)
    reads, writes, copy = [], [], None
    if w == 0:
        return reads, writes, copy
    if op == 0:
        if fn in (0, 2, 3, 56, 58, 59, 60, 62, 63):
            reads, writes = [(rt, 32 if fn < 4 else 64)], [rd]
        elif fn in (4, 6, 7, 20, 22, 23):
            reads, writes = [(rs, 64), (rt, 64)], [rd]
        elif fn in (8, 9):
            reads = [(rs, 64)]
            writes = [rd] if fn == 9 else []
        elif fn in (10, 11):
            reads, writes = [(rs, 64), (rt, 64), (rd, 64)], [rd]
        elif fn in (16, 18):
            writes = [rd]
        elif fn in (17, 19):
            reads = [(rs, 64)]
        elif fn in (24, 25, 26, 27, 28, 29, 30, 31):
            reads = [(rs, 64), (rt, 64)]
            writes = [rd] if fn in (24, 25, 28, 29) else []
        elif fn in (32, 33, 34, 35, 36, 37, 38, 39, 42, 43, 44, 45, 46, 47):
            reads, writes = [(rs, 64), (rt, 64)], [rd]
            # These are low-64 identity moves; ADDU is deliberately excluded
            # because its 32-bit result does not preserve an arbitrary input.
            if fn in (37, 38, 45):
                if rt == 0:
                    copy = (rd, rs, 64)
                elif rs == 0:
                    copy = (rd, rt, 64)
        else:
            return None
    elif op in (2, 3):
        writes = [31] if op == 3 else []
    elif op == 1:
        if rt not in (0, 1, 2, 3):
            return None
        reads = [(rs, 64)]
    elif op in (4, 5, 20, 21):
        reads = [(rs, 64), (rt, 64)]
    elif op in (6, 7, 22, 23):
        reads = [(rs, 64)]
    elif op in (8, 9, 10, 11, 12, 13, 14, 15, 24, 25):
        reads = [] if op == 15 else [(rs, 64)]
        writes = [rt]
        if op in (13, 14, 25) and _imm(w) == 0:
            copy = (rt, rs, 64)
    elif op in (26, 27, 30, 32, 33, 34, 35, 36, 37, 38, 39, 48, 52, 55):
        reads, writes = [(rs, 64)], [rt]
    elif op in (31, 40, 41, 42, 43, 44, 45, 46, 56, 60, 63):
        width = 128 if op == 31 else 64 if op in (44, 45, 63) else 32
        reads = [(rs, 64), (rt, width)]
        writes = [rt] if op in (56, 60) else []
    elif op in (49, 53, 57, 61):
        reads = [(rs, 64)]
    elif op in (17, 18):
        if rs in (0, 1, 2):
            writes = [rt]
        elif rs in (4, 5, 6):
            reads = [(rt, 128 if op == 18 and rs == 5 else 64)]
        elif rs == 8 or op == 17 and rs >= 16:
            pass
        else:
            return None
    else:
        # MMI/COP2 data-path decoding needs instruction-specific 128-bit
        # contracts. Do not silently call its operands ordinary scalar inputs.
        return None
    return reads, writes, copy


def _walk(raw, start):
    output = dict(complete=False, incomingReads={}, calls={}, problems=[])
    if not isinstance(start, int) or start < 0 or start % 4:
        output['problems'].append('Invalid instruction start address.')
        return output
    if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) % 4 or len(raw) // 4 > MAX_WORDS:
        output['problems'].append('Invalid or oversized instruction window.')
        return output
    words = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
    initial = [None] * 32
    for index, reg in enumerate(EE_ARGS):
        initial[reg] = (index, 128)
    pending = deque([(0, tuple(initial))])
    seen = set()
    returns = set()

    def problem(text):
        if text not in output['problems']:
            output['problems'].append(text)

    def step(pc, state):
        effects = _effects(words[pc // 4])
        if effects is None:
            problem(f'Unsupported instruction at +0x{pc:X}.')
            return None
        reads, writes, copy = effects
        for reg, width in reads:
            origin = state[reg] if reg else None
            if origin is not None:
                index, bits = origin
                if bits >= width:
                    row = output['incomingReads'].setdefault(index, dict(requiredBits=0, offsets=[]))
                    row['requiredBits'] = max(row['requiredBits'], width)
                    if pc not in row['offsets']:
                        row['offsets'].append(pc)
                else:
                    problem(f'Narrowed argument provenance consumed as {width}-bit input at +0x{pc:X}.')
        new = list(state)
        for reg in writes:
            if reg:
                new[reg] = None
        if copy:
            dest, source, bits = copy
            if dest and state[source] is not None:
                new[dest] = (state[source][0], min(state[source][1], bits))
        new[0] = None
        return tuple(new)

    def enqueue(pc, state):
        if state is None:
            return
        if pc < 0 or pc >= len(raw) or pc % 4:
            problem('Control flow leaves the supplied instruction window.')
        elif (pc, state) not in seen:
            pending.append((pc, state))

    def iscontrol(w):
        op, rs, rt, rd, fn = _parts(w)
        return op in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23) or op == 0 and fn in (8, 9) or op in (17, 18) and rs == 8

    def delay(pc, state):
        if pc + 4 >= len(raw) or iscontrol(words[pc // 4 + 1]):
            problem('Missing or unsupported control instruction in delay slot.')
            return None
        return step(pc + 4, state)

    while pending:
        pc, state = pending.popleft()
        if (pc, state) in seen:
            continue
        if len(seen) >= MAX_STATES:
            problem('Bounded argument-flow state limit reached.')
            break
        seen.add((pc, state))
        w = words[pc // 4]
        op, rs, rt, rd, fn = _parts(w)
        after = step(pc, state)
        if after is None:
            continue
        if op == 0 and fn == 8:
            if rs != 31:
                problem('Unknown indirect jump.')
            elif delay(pc, after) is not None:
                returns.add(pc)
        elif op == 3 or op == 0 and fn == 9:
            after = delay(pc, after)
            if after is None:
                continue
            if op == 3:
                target = ((start + pc + 4) & 0xf0000000) | ((w & 0x3ffffff) << 2)
                item = output['calls'].setdefault(pc, dict(target=target, states=set()))
                item['states'].add(after)
            # All EE argument registers are caller-clobbered. Saved s0..s7
            # aliases survive, but memory spills are intentionally not guessed.
            clobbered = list(after)
            for reg in (*range(1, 16), 24, 25, 31):
                clobbered[reg] = None
            enqueue(pc + 8, tuple(clobbered))
        elif op == 2:
            target = ((start + pc + 4) & 0xf0000000) | ((w & 0x3ffffff) << 2)
            enqueue(target - start, delay(pc, after))
        elif op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op in (17, 18) and rs == 8:
            likely = op in (20, 21, 22, 23) or op == 1 and rt in (2, 3) or op in (17, 18) and bool(rt & 2)
            known = None
            if op in (4, 20) and rs == rt or op in (6, 22) and rs == 0 or op == 1 and rs == 0 and rt in (1, 3):
                known = True
            if op in (5, 21) and rs == rt or op in (7, 23) and rs == 0 or op == 1 and rs == 0 and rt in (0, 2):
                known = False
            delayed = delay(pc, after) if known is not False or not likely else after
            if known is not False:
                enqueue(pc + 4 + _imm(w) * 4, delayed)
            if known is not True:
                enqueue(pc + 8, after if likely else delayed)
        else:
            enqueue(pc + 4, after)
    output['complete'] = bool(returns) and not output['problems']
    return output


def forwarded_arguments(raw: bytes, start: int, callees: dict[int, bytes]) -> dict:
    """Prove incoming-argument identities used at known direct calls.

    Returns ``findings`` and ``warnings``. A finding requires an identical
    incoming origin on every reachable caller path, sufficient preserved bit
    width and a callee register read before any clobber. This is evidence for
    matching a caller's existing formal expression to a missing call argument;
    it is not permission to guess a prototype or append speculative values.
    """
    caller = _walk(raw, start)
    result = dict(findings=[], warnings=list(caller['problems']))
    if not caller['complete']:
        return result
    cache = {}
    for offset, call in sorted(caller['calls'].items()):
        target = call['target']
        if target not in callees:
            continue
        if target not in cache:
            cache[target] = _walk(callees[target], target)
        callee = cache[target]
        if not callee['complete']:
            result['warnings'].append(f'Callee 0x{target:08X}: argument flow incomplete; no inferred arguments.')
            continue
        for index, use in sorted(callee['incomingReads'].items()):
            reg = EE_ARGS[index]
            values = {state[reg] for state in call['states']}
            if len(values) != 1:
                continue
            value = next(iter(values))
            if value is None or value[1] < use['requiredBits']:
                continue
            result['findings'].append(dict(callOffset=offset, callAddress=f'{start + offset:08X}',
                                           target=f'{target:08X}', argumentIndex=index,
                                           incomingIndex=value[0], preservedBits=value[1],
                                           requiredBits=use['requiredBits'],
                                           calleeReadOffsets=sorted(use['offsets']),
                                           proof='Same incoming value on all caller CFG paths; callee consumes it before clobber.'))
    return result
