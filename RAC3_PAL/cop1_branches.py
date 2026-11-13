"""Decode the four R5900 COP1 branch forms for local rough-input labels."""
import struct

from .ee_common import complete_cfg


def decode(word, address):
    """Return (mnemonic, target) for BC1F/T/FL/TL, or None otherwise.

    Only the zero condition-code field is supported. Nonzero/reserved fields,
    other coprocessors and FP arithmetic remain outside this recovery.
    """
    if (type(word) is not int or not 0 <= word <= 0xffffffff
            or type(address) is not int or not 0 <= address <= 0xffffffff
            or address % 4 or (word & 0xfffc0000) != 0x45000000):
        return None
    displacement = (word & 0x7fff) - (word & 0x8000)
    target = (address + 4 + displacement * 4) & 0xffffffff
    return ('bc1f', 'bc1t', 'bc1fl', 'bc1tl')[word >> 16 & 3], target


def labels(raw, start):
    """Return exact branch offsets only inside a complete local EE function.

    A fragment, external target, incomplete delay slot or control instruction
    in a delay slot retains the original listing; no boundary is invented.
    """
    if (not isinstance(raw, bytes) or not raw or len(raw) % 4
            or type(start) is not int or not 0 <= start <= 0xffffffff
            or start % 4 or start + len(raw) > 0x100000000):
        return {}
    result = {}
    for offset in range(0, len(raw), 4):
        word = struct.unpack_from('<I', raw, offset)[0]
        branch = decode(word, start + offset)
        if branch is None:
            if (word & 0xffe00000) == 0x45000000:
                # Do not let complete_cfg's broader COP1-family recognition
                # stand in for an unsupported R5900 condition-code encoding.
                return {}
            continue
        if not start <= branch[1] < start + len(raw):
            return {}
        result[offset] = branch
    return result if result and complete_cfg(raw, start) else {}
