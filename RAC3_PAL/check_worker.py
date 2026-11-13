"""Isolated RAC3 compile/relocation probe using the repository's real checker."""
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
from types import SimpleNamespace


_LEVEL_DATA_NAME = re.compile(r'D_L(\d\d)_([0-9A-F]{8})(?:_[A-Za-z0-9]+)*\Z')
_MEMORY = {32: (1, False), 33: (2, False), 35: (4, False),
           36: (1, False), 37: (2, False), 39: (4, False),
           40: (1, True), 41: (2, True), 43: (4, True),
           49: (4, False), 57: (4, True)}


def _base_unchanged(data, high, low, register):
    """Fail closed on every GPR write/control edge between one LUI and use.

    R5900 MULT/MULTU have a third GPR destination, unlike MIPS-I. Calls and
    branch joins are deliberately outside this small address proof.
    """
    for offset in range(high + 4, low, 4):
        word = struct.unpack_from('<I', data, offset)[0]
        op, rs, rt, rd, fn = word >> 26, word >> 21 & 31, word >> 16 & 31, word >> 11 & 31, word & 63
        if word == 0:
            continue
        if op == 0:
            if fn in (0, 2, 3, 4, 6, 7, 10, 11, 16, 18, 20, 22, 23,
                      24, 25, 32, 33, 34, 35, 36, 37, 38, 39, 42, 43,
                      44, 45, 46, 47, 56, 58, 59, 60, 62, 63):
                if rd == register:
                    return False
            elif fn not in (17, 19, 26, 27):
                return False
        elif op in (8, 9, 10, 11, 12, 13, 14, 15, 24, 25, 26, 27,
                    30, 32, 33, 34, 35, 36, 37, 38, 39, 55):
            if rt == register:
                return False
        elif op == 17:
            if rs in (0, 2):
                if rt == register:
                    return False
            elif rs not in (4, 6, 16, 17, 20, 21, 22):
                return False
        elif op not in (31, 40, 41, 42, 43, 44, 45, 46, 49, 53, 57, 61, 63):
            return False
    return True


def _in_data(reference, address, width=1, write=False):
    return (type(address) is int and 0 <= address and width > 0
            and address % width == 0 and address + width <= 0x100000000
            and any(start <= address and address + width <= end and (not write or writable)
                    for start, end, writable in reference['data']))


def _named_level_targets(body, relocations, references, primary, derive, blocked_symbols=()):
    """Prove unknown level globals from the existing project's derive policy.

    Only complete HI16/LO16 address groups with an actual named primary anchor
    are eligible. Every group keeps its original instruction offsets, register
    fields and addends at every catalogue placement. Other instructions and
    unresolved relocation formats remain subject to the normal raw comparison.
    Returning a target is never a byte, ownership or semantic approval.
    """
    if (not references or primary not in references or len(body) % 4
            or len({r[0] for r in relocations}) != len(relocations)):
        return {}, []
    mapped, evidence = {}, []
    symbols = sorted({symbol for _, _, symbol in relocations if _LEVEL_DATA_NAME.fullmatch(symbol)})
    for symbol in symbols:
        match = _LEVEL_DATA_NAME.fullmatch(symbol)
        if int(match[1]) != primary or symbol in blocked_symbols:
            continue
        records = [(off, kind, name) for off, kind, name in relocations if name == symbol]
        highs = [off for off, kind, _ in records if kind == 5]
        lows = [off for off, kind, _ in records if kind == 6]
        if (len(highs) != 1 or not lows or any(kind not in (5, 6) for _, kind, _ in records)
                or any(off < 0 or off % 4 or off + 4 > len(body) for off, _, _ in records)
                or any(off <= highs[0] for off in lows)):
            continue
        high = highs[0]
        high_word = struct.unpack_from('<I', body, high)[0]
        register = high_word >> 16 & 31
        if high_word >> 26 != 15 or high_word >> 21 & 31 or register in (0, 28, 29, 31):
            continue
        use_fields = []
        for low in lows:
            word = struct.unpack_from('<I', body, low)[0]
            op, base = word >> 26, word >> 21 & 31
            if (op not in _MEMORY or base != register
                    or not _base_unchanged(body, high, low, register)):
                break
            use_fields.append((low, word, *_MEMORY[op]))
        if len(use_fields) != len(lows):
            continue
        order = [high, *lows]
        packed = b''.join(body[off:off + 4] for off in order)
        group = [(0, 5, symbol)] + [(4 * (i + 1), 6, symbol) for i in range(len(lows))]
        proven = []
        for number, reference in references.items():
            native = reference['raw']
            gp = reference.get('gp')
            if (not isinstance(gp, int) or isinstance(gp, bool) or not 0 < gp <= 0xffffffff
                    or len(native) < len(body) or any(off + 4 > len(native) for off in order)):
                break
            native_words = [struct.unpack_from('<I', native, off)[0] for off in order]
            if (any((ours ^ theirs) & 0xffff0000 for ours, theirs in zip(
                    [high_word, *[word for _, word, _, _ in use_fields]], native_words))
                    or any(not _base_unchanged(native, high, low, register) for low in lows)):
                break
            try:
                values = derive(packed, b''.join(native[off:off + 4] for off in order),
                                group, reference['address'] + high, gp)
                target = values[symbol]
            except Exception:
                break
            if (set(values) != {symbol} or not 0 <= target <= 0xffffffff
                    or number == primary and target != int(match[2], 16)
                    or not _in_data(reference, target)):
                break
            uses = []
            for (low, _, width, write), word in zip(use_fields, native_words[1:]):
                signed_low = (word & 0x7fff) - (word & 0x8000)
                actual = ((native_words[0] & 0xffff) << 16) + signed_low
                actual &= 0xffffffff
                if not _in_data(reference, actual, width, write):
                    break
                uses.append(dict(offset=low, address=actual, width=width, write=write))
            if len(uses) != len(lows):
                break
            proven.append(dict(level=number, target=target, address=reference['address'],
                               offsets=order, uses=uses))
        if len(proven) == len(references):
            mapped[symbol] = {row['level']: row['target'] for row in proven}
            evidence.append(dict(symbol=symbol, primaryLevel=primary,
                                 policy='tools.level_symbols.derive', placements=proven))
    return mapped, evidence


def _level_references(name):
    """Hash-, ELF-, GP- and catalogue-checked complete placement references."""
    import gp
    import level_catalogue
    import targets
    from elftools.elf.elffile import ELFFile
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from RAC3_PAL.ee_common import complete_cfg
    match = re.fullmatch(r'func_L(\d\d)_([0-9A-F]{8})', name)
    if not match:
        return None
    rows = [row for row in level_catalogue.read_catalogue() if row[0] == name]
    if len(rows) != 1:
        return None
    row = rows[0]
    primary = int(match[1])
    if (len({number for number, _ in row[5]}) != len(row[5])
            or dict(row[5]).get(primary) != int(match[2], 16)):
        return None
    references = {}
    for number, address in row[5]:
        target = targets.get('level_%02d' % number)
        blob = Path(target.path('elf')).read_bytes()
        elf = ELFFile(io.BytesIO(blob))
        actual_gp, _ = gp.from_elf(str(target.path('elf')))
        if (hashlib.sha1(blob).hexdigest() != target.sha1 or elf.elfclass != 32
                or not elf.little_endian or elf['e_machine'] != 'EM_MIPS'
                or actual_gp is None or actual_gp != target.gp):
            return None
        text = elf.get_section_by_name('.text')
        if (text is None or not text['sh_addr'] <= address
                or address + row[2] > text['sh_addr'] + text['sh_size']):
            return None
        raw = text.data()[address - text['sh_addr']:address - text['sh_addr'] + row[2]]
        if not complete_cfg(raw, address):
            return None
        data = [(sec['sh_addr'], sec['sh_addr'] + sec['sh_size'], bool(sec['sh_flags'] & 1))
                for sec in elf.iter_sections() if sec['sh_flags'] & 2 and not sec['sh_flags'] & 4
                and sec['sh_type'] in ('SHT_PROGBITS', 'SHT_NOBITS')]
        references[number] = dict(address=address, raw=raw, gp=actual_gp, data=data)
    return primary, references


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--target', required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--object', type=Path)
    ap.add_argument('--address', type=int)
    ap.add_argument('--size', type=int)
    ap.add_argument('--frontbin-copy', action='store_true')
    a = ap.parse_args()
    if not re.fullmatch(r'func_(?:L\d\d_)?[0-9A-Fa-f]{8}', a.name):
        raise ValueError('Invalid function name')
    root = a.repo.resolve()
    os.chdir(root)
    os.environ['UYA_TARGET'] = a.target
    os.environ['UYA_VERSION'] = 'pal'
    sys.path.insert(0, str(root / 'tools'))
    import targets
    import check
    from elftools.elf.elffile import ELFFile
    t = targets.get(a.target)
    if a.frontbin_copy:
        if not a.object or a.target != 'boot_elf':
            raise ValueError('Invalid front-end copy request')
        import level_catalogue
        twins = {front: int(boot[5:], 16) for boot, front in level_catalogue.read_pairs().items()}
        original_address = check.symbol_address
        def boot_symbol(symbol):
            if symbol in twins:
                return twins[symbol]
            m = re.fullmatch(r'func_([0-9A-F]{8})', symbol)
            if m and targets.get('frontbin').unit_for(int(m[1], 16)):
                return None  # no catalogue proof for this copy's callee
            return original_address(symbol)
        check.symbol_address = boot_symbol
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        if targets.is_level(t) and not check.LEVEL_FUNC.fullmatch(a.name):
            # A front-end function's catalogue-proven level copy: resolve the
            # original object's relocations for that level without recompiling.
            if not a.object:
                raise ValueError('Front-end copies use the primary compiled object')
            import level_catalogue, level_symbols, level_tree
            rows = level_catalogue.read_catalogue()
            row = next(r for r in rows if r[0] == a.name)
            address, size = dict(row[5])[t.level], row[2]
            check.LEVEL.update(places={a.name: (address, size)},
                               names=level_symbols.Level(t.level, rows, t.gp),
                               context='', fe=level_tree.front_end_names(rows))
            flags = []
        elif check.LEVEL_FUNC.fullmatch(a.name):
            t, flags = check.level_function([a.name], t)
            address, size = check.LEVEL['places'][a.name]
        else:
            address = int(a.name[5:], 16)
            flags = check.flags_for(address)
            size = check.retail_size(a.name, 0)
        if a.object and a.address is not None and a.size is not None:
            address, size = a.address, a.size
        if not size:
            raise ValueError('Function boundary is not catalogued')
        args = SimpleNamespace(toolchain=str(root / 'build/toolchains/sn'),
                               ee29=str(root / 'build/toolchains/ee29'), runner=None,
                               no_context=False, early_extern_sizes={})
        obj = str(a.object) if a.object else check.compile_c(str(a.source), flags, args, a.name)
        if not obj:
            raise ValueError(capture.getvalue())
        out = a.out.resolve()
        out.mkdir(parents=True, exist_ok=True)
        shutil.copy2(obj, out / 'candidate.o')
        with open(obj, 'rb') as stream:
            elf = ELFFile(stream)
            from elftools.elf.relocation import RelocationSection
            text = elf.get_section_by_name('.text')
            symbols = elf.get_section_by_name('.symtab')
            sym = next((s for s in symbols.iter_symbols() if s.name == a.name), None)
            if sym is None or sym['st_shndx'] != elf.get_section_index('.text'):
                raise ValueError('Function is missing from the object text section')
            start, length = sym['st_value'], sym['st_size']
            raw = bytearray(text.data())
            resolved, masks = check.resolve_relocations(elf, raw)
            derived_targets, target_evidence = {}, []
            if masks and targets.is_level(t) and check.LEVEL_FUNC.fullmatch(a.name):
                try:
                    import level_symbols
                    references = _level_references(a.name)
                    records, blocked_names = [], set()
                    for sec in elf.iter_sections():
                        if not isinstance(sec, RelocationSection) or sec['sh_info'] != elf.get_section_index('.text'):
                            continue
                        if sec.is_RELA():
                            raise ValueError('Named level target proof supports REL only')
                        for rel in sec.iter_relocations():
                            off = rel['r_offset']
                            symbol = elf.get_section(sec['sh_link']).get_symbol(rel['r_info_sym'])
                            if start <= off < start + length:
                                records.append((off - start, rel['r_info_type'], symbol.name))
                                if symbol['st_shndx'] != 'SHN_UNDEF':
                                    blocked_names.add(symbol.name)
                    if references:
                        primary, reference_map = references
                        current = reference_map.get(t.level)
                        if current is None or address != current['address'] or size != len(current['raw']):
                            raise ValueError('Comparison does not use the catalogue placement')
                        derived_targets, target_evidence = _named_level_targets(
                            bytes(raw[start:start + length]), records, reference_map, primary,
                            level_symbols.derive, blocked_names)
                    original_address = check.symbol_address
                    addresses = {symbol: values[t.level] for symbol, values in derived_targets.items()}
                    if any(original_address(symbol) not in (None, address) for symbol, address in addresses.items()):
                        raise ValueError('Derived target contradicts an existing symbol')
                    try:
                        check.symbol_address = lambda symbol: addresses.get(symbol, original_address(symbol))
                        resolved, masks = check.resolve_relocations(elf, raw)
                    finally:
                        check.symbol_address = original_address
                except Exception:
                    # Unsupported formats or incomplete placement evidence leave
                    # the original unresolved fields and comparison untouched.
                    derived_targets, target_evidence = {}, []
            for off, value in resolved.items():
                struct.pack_into('<I', raw, off, value)
            body = bytes(raw[start:start + length])
            unresolved = [hex(off - start) for off in masks if start <= off < start + length]
            data = [{'name': sec.name, 'size': sec['sh_size']} for sec in elf.iter_sections()
                    if sec['sh_flags'] & 2 and sec.name != '.text' and sec['sh_size']]
            extra = [s.name for s in symbols.iter_symbols() if isinstance(s['st_shndx'], int)
                     and s.name != a.name and (s['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT')
                                              or s['st_info']['bind'] == 'STB_GLOBAL')]
            relocations, covered = [], []
            for sec in elf.iter_sections():
                if not isinstance(sec, RelocationSection):
                    continue
                for rel in sec.iter_relocations():
                    off, kind = rel['r_offset'], rel['r_info_type']
                    if sec['sh_info'] != elf.get_section_index('.text'):
                        continue
                    if not start <= off < start + length:
                        unresolved.append(hex(off - start))
                        continue
                    relative = off - start
                    covered.append([relative, kind])
                    relocations.append(dict(offset=relative, type=kind,
                        symbol=elf.get_section(sec['sh_link']).get_symbol(rel['r_info_sym']).name,
                        expected=body[relative:relative + 4].hex(),
                        actual='', ok=hex(relative) not in unresolved))
            outside = raw[:start] + raw[start + length:]
            if any(outside) or start or len(outside) >= 8:
                extra.append('<additional text outside the function>')
        retail = check.Retail(t.path('elf')).read(address, size)
        if len(retail) != size:
            raise ValueError('Retail function extends outside the program')
        (out / 'target.bin').write_bytes(retail)
        (out / 'source.bin').write_bytes(body)
        exact = body == retail and not unresolved
        for relocation in relocations:
            off = relocation['offset']
            relocation['actual'] = retail[off:off + 4].hex()
            relocation['ok'] = relocation['ok'] and relocation['expected'] == relocation['actual']
        meta = {'ok': True, 'name': a.name, 'target': t.name, 'flags': flags,
                'address': address, 'retailSize': size, 'compiledSize': length,
                'technicalExact': exact, 'unresolvedRelocations': unresolved,
                'objectHash': hashlib.sha256(Path(obj).read_bytes()).hexdigest(),
                'resolvedBodyHash': hashlib.sha256(body).hexdigest(),
                'dataSections': data, 'extraDefinitions': extra, 'relocations': relocations,
                'coveredRelocations': covered, 'reviewRequired': True,
                'derivedLevelTargets': target_evidence,
                'log': capture.getvalue(), 'directory': str(out)}
        (out / 'evidence.json').write_text(json.dumps(meta, indent=2), encoding='utf-8', newline='\n')
        (out / 'compile.log').write_text(capture.getvalue(), encoding='utf-8', newline='\n')
    print(json.dumps(meta))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'ok': False, 'message': str(exc)}))
        sys.exit(1)
