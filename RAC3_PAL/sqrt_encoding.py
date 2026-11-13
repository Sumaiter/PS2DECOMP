"""Recover R5900 SQRT.S, whose source is FT rather than generic MIPS FS."""
import re


def fields(word):
    """Exact single-format EE encoding, including the zero unused FS field."""
    if not isinstance(word, int) or not 0 <= word <= 0xffffffff or word & 0xffe0f83f != 0x46000004:
        return None
    return word >> 6 & 31, word >> 16 & 31


def normalize(assembly):
    """Rewrite only raw disassembler operands agreeing with their word comment.

    This adapter is PAL EE-specific. Generic MIPS FS-form SQRT, other formats,
    unrelated COP1 opcodes and existing textual instructions remain unchanged.
    """
    evidence = []
    pattern = re.compile(r'(?m)^(/\* ([0-9A-F]{8}) ([0-9A-F]{8}) \*/[ \t]*)(c1|\.word)[ \t]+0x([0-9A-Fa-f]{1,8})[ \t]*$')
    def replace(match):
        word = int(match[3], 16)
        registers = fields(word)
        rendered = word if match[4] == '.word' else word & 0x01ffffff
        if registers is None or rendered != int(match[5], 16):
            return match[0]
        fd, ft = registers
        instruction = f'sqrt.s $f{fd}, $f{ft}'
        evidence.append(dict(address=match[2], word=match[3], instruction=instruction,
                             encoding='R5900 COP1 S: source FT; unused FS zero'))
        return match[1] + instruction
    return pattern.sub(replace, assembly), evidence
