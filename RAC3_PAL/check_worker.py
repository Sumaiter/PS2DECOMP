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
