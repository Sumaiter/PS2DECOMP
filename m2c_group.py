"""Explicit, bounded multi-function m2c previews. No drafts or searches are created."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import local_process
import rough

MAX_FUNCTIONS = 8
MAX_BYTES = 65536


def selection(engine, name, names):
    if (not isinstance(names, list) or not 2 <= len(names) <= MAX_FUNCTIONS
            or any(not isinstance(n, str) for n in names)
            or len(set(names)) != len(names) or name not in names):
        raise ValueError('Select 2 to 8 distinct functions, including the current function.')
    program = engine.place(name)[0]
    if any(engine.place(n)[0] != program for n in names):
        raise ValueError('Group analysis requires canonical functions from the same program.')
    if sum(engine.place(n)[3] for n in names) > MAX_BYTES:
        raise ValueError('Group analysis exceeds the 64 KiB input limit.')
    # Reject alias windows even when their canonical names differ.
    windows = []
    for n in names:
        for p, section, start, size in engine.all_places(n):
            if any(p == q and section == s and start < end and begin < start + size
                   and other != n for q, s, begin, end, other in windows):
                raise ValueError('Selected functions have overlapping reference windows.')
            windows.append((p, section, start, start + size, n))
    return names


def available(engine, names):
    from auto_jobs import PlacementIndex
    inventory = engine.inventory_state()
    index = PlacementIndex(engine, inventory)
    for n in names:
        engine.assert_access(n)
        if index.overlaps(engine.all_places(n), n):
            raise ValueError(n + ': reference window is already mapped or reserved.')


def candidates(engine, name):
    """List known direct callees without reading or claiming their bodies."""
    engine.assert_access(name)
    program = engine.place(name)[0]
    names = re.findall(r'(?m)^/\* [0-9A-F]+ [0-9A-F]+ \*/\s*jal\s+(\w+)\s*$', engine.asm(name))
    return [dict(name=n, size=engine.place(n)[3]) for n in sorted(set(names) - {name})
            if n in engine.places and engine.place(n)[0] == program]


def decompile(engine, name, names, timeout=45):
    names = selection(engine, name, names)
    script = rough.tool(engine.root)
    if script is None:
        raise ValueError('Local m2c is missing under build/ext/m2c or tools/ext/m2c.')
    deadline = time.monotonic() + timeout
    with ExitStack() as leases:
        available(engine, names)
        for n in sorted(names):
            leases.enter_context(engine.manual_lease(n))
        available(engine, names)
        pins = engine.pin_inputs(names)
        prepared = []
        from ee_common import complete_cfg
        for n in names:
            for place in engine.all_places(n):
                if not complete_cfg(engine.check.disc_bytes(place), place[2]):
                    raise ValueError(n + ': incomplete or unsupported function boundary.')
            if time.monotonic() >= deadline:
                raise ValueError('m2c group time limit reached during input preparation.')
            prepared.append(rough.decompile(engine, n, prepare_only=True))
        folder = rough.directory(engine.root, name, 'decompile-group')
        inputs = []
        for item in prepared:
            path = Path(item['directory']) / 'input.s'
            inputs.append(dict(name=item['name'], path=str(path),
                               sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        # Merge authoritative contracts again rather than merging inferred output.
        referenced = set(names)
        for item in inputs:
            referenced.update(re.findall(r'\bfunc_(?:L\d\d_)?[0-9A-F]{8}\b',
                                          Path(item['path']).read_text(encoding='utf-8')))
        declarations, warnings = rough.context(engine.contracts, referenced)
        context = folder / 'context.c'
        context.write_text(rough.TYPES + '\n'.join(declarations) + '\n', encoding='utf-8')
        context_hash = hashlib.sha256(context.read_bytes()).hexdigest()
        command = [sys.executable, str(script), '-t', 'mipsee-gcc-c', '--valid-syntax',
                   '--no-cache', '--context', str(context), '--passes', '2']
        for n in names:
            command += ['-f', n]
        command += [item['path'] for item in inputs]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('m2c group time limit reached during input preparation.')
        try:
            run = local_process.run(command, cwd=folder, text=True, encoding='utf-8',
                                    errors='replace', timeout=remaining)
            output, log, code = run.stdout, run.stderr, run.returncode
        except subprocess.TimeoutExpired:
            output, log, code = '', 'm2c group time limit reached.', -1
        (folder / 'm2c-output.c').write_text(output, encoding='utf-8')
        (folder / 'm2c.log').write_text(log, encoding='utf-8')
        if (engine.pin_inputs(names) != pins
                or hashlib.sha256(context.read_bytes()).hexdigest() != context_hash
                or any(hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256']
                       for item in inputs)):
            raise ValueError('Group inputs changed; discard this preview and run again.')
        available(engine, names)
        ok = code == 0 and all(re.search(r'\b' + re.escape(n) + r'\s*\([^;]*\)\s*\{', output) for n in names)
        # A joint output is deliberately separate from each single-function draft.
        preview = folder / 'group-preview.c'
        preview.write_text(('/* Unverified m2c group analysis. Review every type, ABI and function. */\n'
                            if ok else '/* INCOMPLETE m2c group analysis. Inspect m2c.log before using any output. */\n')
                           + '\n'.join(declarations) + '\n' + output, encoding='utf-8')
        result = dict(name=name, names=names, ok=ok, preview=str(preview), directory=str(folder),
                      command=command, inputs=inputs, pins=pins, preparation=prepared,
                      contextHash=context_hash,
                      contextDeclarations=declarations, warnings=warnings, log=log[-20000:],
                      returncode=code, adapterVersion=rough.ADAPTER_VERSION)
        (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return result
