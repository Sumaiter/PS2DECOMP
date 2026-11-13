import struct
import re

def complete_cfg(raw, start):
    """Reject fragments, tail exits, traps and control instructions in delay slots."""
    if not raw or len(raw) % 4:
        return False
    words = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
    pending, seen, returns = [0], set(), 0
    def control(w):
        op, rs, fn = w >> 26, w >> 21 & 31, w & 63
        return op in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23) or op == 0 and fn in (8, 9, 12, 13, 48, 49, 50, 51, 52, 54) or op == 16 or op in (17, 18) and rs == 8
    def enqueue(offset):
        if offset % 4 or not 0 <= offset < len(raw):
            raise ValueError('CFG außerhalb der Funktion')
        if offset not in seen:
            pending.append(offset)
    try:
        while pending:
            pc = pending.pop()
            if pc in seen:
                continue
            seen.add(pc); w = words[pc // 4]
            op, rs, rt, fn = w >> 26, w >> 21 & 31, w >> 16 & 31, w & 63
            if op in (2, 16) or op == 0 and fn in (12, 13, 48, 49, 50, 51, 52, 54):
                return False
            if op == 1 and rt not in (0, 1, 2, 3):
                return False
            branch = op in (1, 4, 5, 6, 7, 20, 21, 22, 23) or op in (17, 18) and rs == 8
            call = op == 3 or op == 0 and fn == 9
            ret = op == 0 and fn == 8
            if branch or call or ret:
                if pc + 4 >= len(raw) or control(words[pc // 4 + 1]):
                    return False
                seen.add(pc + 4)
            if ret:
                if rs != 31:
                    return False
                returns += 1
            elif call:
                enqueue(pc + 8)
            elif branch:
                imm = (w & 0x7fff) - (w & 0x8000)
                known = None
                if op in (4, 20) and rs == rt or op in (6, 22) and rs == 0 or op == 1 and rs == 0 and rt in (1, 3):
                    known = True
                if op in (5, 21) and rs == rt or op in (7, 23) and rs == 0 or op == 1 and rs == 0 and rt in (0, 2):
                    known = False
                if known is not False:
                    enqueue(pc + 4 + imm * 4)
                if known is not True:
                    enqueue(pc + 8)
            else:
                enqueue(pc + 4)
        return bool(returns) and not any(w and i * 4 not in seen for i, w in enumerate(words))
    except ValueError:
        return False

def similarity(result):
    """Instruction edit similarity is a search score, never a correctness percentage."""
    rows = result.get('rows', [])
    left = [(r['oursHex'], r['ours'].split()[0], int(r['mask'], 16)) for r in rows if r['oursHex'] != '—']
    right = [(r['retailHex'], r['retail'].split()[0], int(r['mask'], 16)) for r in rows if r['retailHex'] != '—']
    def token(item):
        raw, op, mask = item
        return int.from_bytes(bytes.fromhex(raw), 'little') & ~mask, op
    a, b = list(map(token, left)), list(map(token, right))
    if not a or not b:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, (value, op) in enumerate(a, 1):
        cur = [float(i)]
        for j, (other, other_op) in enumerate(b, 1):
            cost = 0 if value == other else .35 if op == other_op else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return round(max(0, 100 * (1 - prev[-1] / max(len(a), len(b)))), 3)
