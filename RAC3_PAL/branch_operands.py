"""Recover raw integer branch operands for local RAC3 rough-listing labels."""
import struct

from .ee_common import complete_cfg


_GPRS = ('zero', 'at', 'v0', 'v1', 'a0', 'a1', 'a2', 'a3',
         't0', 't1', 't2', 't3', 't4', 't5', 't6', 't7',
         's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7',
         't8', 't9', 'k0', 'k1', 'gp', 'sp', 'fp', 'ra')


def decode_integer(word, address):
    """Return the condition, unchanged register operands and signed target.

    Only the nonlink integer branch forms recognized by the existing CFG
    screen are supported. Link forms, jumps and coprocessors are separate.
    """
    if (type(word) is not int or not 0 <= word <= 0xffffffff
            or type(address) is not int or not 0 <= address <= 0xffffffff
            or address % 4):
        return None
    op, rs, rt = word >> 26, word >> 21 & 31, word >> 16 & 31
    if op == 1 and rt in (0, 1, 2, 3):
        mnemonic, registers = ('bltz', 'bgez', 'bltzl', 'bgezl')[rt], (rs,)
    elif op in (4, 5, 20, 21):
        mnemonic, registers = {4: 'beq', 5: 'bne', 20: 'beql', 21: 'bnel'}[op], (rs, rt)
        if op == 4 and rs == rt == 0:
            mnemonic, registers = 'b', ()
        elif op in (4, 5) and rt == 0:
            mnemonic, registers = ('beqz' if op == 4 else 'bnez'), (rs,)
    elif op in (6, 7, 22, 23) and rt == 0:
        mnemonic, registers = {6: 'blez', 7: 'bgtz', 22: 'blezl', 23: 'bgtzl'}[op], (rs,)
    else:
        return None
    displacement = (word & 0x7fff) - (word & 0x8000)
    return mnemonic, tuple(_GPRS[register] for register in registers), (address + 4 + displacement * 4) & 0xffffffff


def integer_labels(raw, start):
    """Offer local operands only for a complete, nonwrapping function window."""
    if (not isinstance(raw, bytes) or not raw or len(raw) % 4
            or type(start) is not int or not 0 <= start <= 0xffffffff
            or start % 4 or start + len(raw) > 0x100000000):
        return {}
    result = {}
    for offset in range(0, len(raw), 4):
        word = struct.unpack_from('<I', raw, offset)[0]
        branch = decode_integer(word, start + offset)
        if branch is None:
            continue
        if not start <= branch[2] < start + len(raw):
            return {}
        result[offset] = branch
    return result if result and complete_cfg(raw, start) else {}


def decode_bnez(word, address):
    """Restricted BNE-to-zero view retained for local protocol callers."""
    decoded = decode_integer(word, address)
    if decoded is not None and decoded[0] == 'bnez' and decoded[1] != ('zero',):
        return decoded[1][0], decoded[2]
    return None


def bnez_labels(raw, start):
    return {offset: (registers[0], target)
            for offset, (mnemonic, registers, target) in integer_labels(raw, start).items()
            if mnemonic == 'bnez' and registers != ('zero',)}
