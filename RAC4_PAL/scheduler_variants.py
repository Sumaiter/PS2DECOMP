"""Bounded instruction-scheduling trials; C semantics and ABI remain unchanged."""
import re
import struct

OPTIONS = ('-fno-schedule-insns -fno-schedule-insns2',
           '-fno-schedule-insns', '-fno-schedule-insns2')


def straight_memory_leaf(raw):
    """Only whole, short linear leaves with a real JR delay slot and >=3 accesses."""
    if not raw or len(raw) % 4 or not 20 <= len(raw) <= 256:
        return False
    words = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
    if words[-2] != 0x03e00008:
        return False
    memory = {26, 27, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39,
              40, 41, 42, 43, 44, 45, 46, 49, 53, 55, 57, 61, 63}
    count = 0
    for i, w in enumerate(words):
        op, rs, fn = w >> 26, (w >> 21) & 31, w & 63
        if i == len(words) - 2:
            continue
        # Reject all branches/calls/traps, coprocessor control and arithmetic;
        # only ordinary address/constant construction and loads/stores here.
        if op == 0:
            if (w >> 11) & 31 in (29, 31):
                return False
            if fn not in (0, 2, 3, 4, 6, 7, 32, 33, 34, 35, 36, 37, 38, 39, 42, 43, 45, 47, 60, 62, 63):
                return False
        elif op not in memory | {9, 10, 11, 12, 13, 14, 15}:
            return False
        elif op not in memory and (w >> 16) & 31 in (29, 31):
            return False
        if op in memory:
            # A function writing SP/RA or touching stack locals is left manual.
            if rs in (29, 31):
                return False
            count += 1
    return count >= 3


def generate(source, raw):
    if not straight_memory_leaf(raw):
        return
    flags = re.findall(r'/\*\s*cflags:\s*(.*?)\s*\*/', source, re.S)
    if len(flags) > 1:
        return
    old = flags[0].split() if flags else []
    # Keep exact special profiles/options on their recorded path. Do not mix
    # original-compiler ABI/FP profiles or explore descendants of our options.
    if old not in ([], ['@ee29']):
        return
    body = re.sub(r'/\*\s*cflags:.*?\*/\s*', '', source, count=1, flags=re.S) if flags else source
    for option in OPTIONS:
        label = 'instruction-scheduling: ' + option
        yield label, '/* cflags: ' + ' '.join(old + option.split()) + ' */\n' + body
