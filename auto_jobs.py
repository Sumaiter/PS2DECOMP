"""Persistent local search queue and CLI. Shared PS2DECOMP claims cover queued AND finished jobs."""
from __future__ import annotations
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import multiprocessing
from collections import deque
from contextlib import contextmanager
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid
import search_time
from bisect import bisect_left

import candidates
import rough
import candidate_preflight
import function_size
import pointer_seeds
import structural_seeds
import address_variants
import review_variants
import index_base_variants
import native_guard_variants
import terminal_return_variants
import reset_store_variants
import counter_relay_variants
import retry_context
import reviewed_seeds
import retention
import postcall_default_variants
import native_load_before_clear
import adjacent_input_store_variants
import shared_call_join_variants
import word_base_variants
import word_store_address_variants
import terminated_switch_variants
import native_sd16_profile
import compiler_variants
import scalar_copy_temp
import scalar_definition_retarget
import postcall_integer_default
import terminal_scalar_parameter
import disjoint_store_swap
import scheduler_variants
from seed_normalize import normalize
from auto_engine import AutoEngine, FUNCTION, complete_cfg, similarity
from search_rules import Variants, code_only, source_key

ID = re.compile(r'[0-9a-f]{32}\Z')
# Review08: all 301 observed best candidates (17 strict technical matches) were
# reached by attempt8 and58.365s. Keep headroom rather than stop at a plateau.
DEFAULTS = dict(workers=16, maxAttempts=30, seconds=90, stall=20, target=90, nearAfter=15, **function_size.DEFAULTS)
LIMITS = dict(workers=(1, 32), maxAttempts=(1, 2000), seconds=(5, 3600), stall=(1, 200), target=(80, 99), nearAfter=(1, 2000))
FINAL = {'review_ready', 'blocked', 'error', 'handed_off'}
DEFAULT_LOAD_LIMIT = 1000


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextmanager
def file_lock(path):
    """OS lock: automatically released on crashes; never steal by age or delete it."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    # No buffered write may be retried by close() after the region is unlocked.
    with path.open('a+b', buffering=0) as stream:
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                # CRT byte locks may extend past EOF. Acquire before the first
                # write: concurrent append seeks on an empty file can otherwise
                # race a winner's mandatory Windows byte-range lock.
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Sitzung ist beschäftigt; laufenden Vorgang zuerst beenden.')
        try:
            # Retain the existing guard-byte format, but initialize it only
            # under the acquired lock and inspect the open file, not its path.
            if not os.fstat(stream.fileno()).st_size:
                stream.write(b'0'); stream.flush()
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def read(path):
    # Status polling and atomic replacement can overlap with short Windows
    # sharing locks. Retry the read, never substitute stale/default job data.
    deadline = time.monotonic() + 2.0
    while True:
        try:
            text = Path(path).read_text(encoding='utf-8')
            break
        except PermissionError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(.05, remaining))
    return json.loads(text)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        for attempt in range(15):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt == 14:
                    raise
                time.sleep(.03)
    finally:
        tmp.unlink(missing_ok=True)


def options(value=None):
    result = dict(DEFAULTS)
    result.update(function_size.settings(value))
    for key, bounds in LIMITS.items():
        if key in (value or {}):
            v = value[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or not bounds[0] <= v <= bounds[1]:
                raise ValueError(f'Ungültige Einstellung {key}: {bounds[0]}–{bounds[1]} erforderlich.')
            result[key] = int(v)
    return result


def process_birth(pid):
    """Stable creation identity, so a recycled Windows PID is never our old runner."""
    if not pid:
        return None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return 'unreadable' if ctypes.get_last_error() == 5 else None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return 'unreadable'
            return str(times[0].dwHighDateTime << 32 | times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    try:
        text = Path(f'/proc/{int(pid)}/stat').read_text()
        return text[text.rfind(')') + 2:].split()[19]
    except OSError:
        return 'unreadable'


def live_process(pid, birth=None):
    if not pid:
        return False
    if birth:
        actual = process_birth(pid)
        if actual not in (birth, 'unreadable'):
            return False
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            # Access denied is not evidence that another process has stopped.
            return ctypes.get_last_error() == 5
        try:
            code = ctypes.c_ulong()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def claim_api(engine):
    from project_claims import ClaimAPI
    return ClaimAPI(engine.root)


def previous_jobs(engine, names=None, progress=None):
    """Read relevant history once per function; never delete or edit old jobs.

    A targeted scan may stop once every requested function is already excluded.
    An untouched newer handoff does not hide an older attempted job.
    """
    wanted = None if names is None else set(names)
    if wanted == set():
        return set()
    done = set()
    root = engine.root / 'build/autodecomp'
    try:
        with os.scandir(root) as entries:
            sessions = sorted((e for e in entries if e.is_dir()),
                              key=lambda e: e.stat().st_mtime_ns, reverse=True)
    except FileNotFoundError:
        return done
    checked = 0
    for session in sessions:
        try:
            entries = os.scandir(Path(session.path) / 'jobs')
        except (FileNotFoundError, NotADirectoryError):
            continue
        with entries:
            for entry in entries:
                if not entry.is_dir() or wanted is not None and (entry.name not in wanted or entry.name in done):
                    continue
                try:
                    job = read(Path(entry.path) / 'job.json'); checked += 1
                    if not isinstance(job, dict):
                        raise ValueError('Invalid job metadata.')
                    if job.get('name') != entry.name:
                        # Corrupt identity is not evidence that this function is
                        # free. Preserve the file and exclude its own directory.
                        done.add(entry.name)
                    if job.get('attempts', 0) or job.get('deferred') or job.get('status') in ('blocked', 'error', 'review_ready'):
                        done.add(job['name'])
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                if progress and checked % 100 == 0:
                    progress(dict(stage='history', checked=checked, excluded=len(done)))
                if wanted is not None and wanted <= done:
                    if progress:
                        progress(dict(stage='history', checked=checked, excluded=len(done)))
                    return done
    if progress:
        progress(dict(stage='history', checked=checked, excluded=len(done)))
    return done


def is_deferred(job):
    """Legacy blocked jobs are also deferred; preserve this across review handoff."""
    return job.get('deferred', False) or job.get('status') == 'blocked'


class PlacementIndex:
    """One inventory snapshot, with all canonical and claimed overlay intervals.

    Rebuild for each validation pass; never cache a claim across passes.
    Prefix maximum ends retain nested/long intervals during backwards queries.
    """
    def __init__(self, engine, inventory):
        groups = {}
        for row in inventory[0].values():
            for program, section, start, size in row['places']:
                groups.setdefault((program, section), []).append((start, start + size, None))
        for name in inventory[3]:
            if name in engine.places:
                for program, section, start, size in engine.all_places(name):
                    groups.setdefault((program, section), []).append((start, start + size, name))
        self.groups = {}
        for key, values in groups.items():
            values.sort(key=lambda row: row[0])
            ends, largest = [], -1
            for _, end, _ in values:
                largest = max(largest, end); ends.append(largest)
            self.groups[key] = ([row[0] for row in values], values, ends)

    def overlaps(self, places, name):
        for program, section, start, size in places:
            starts, values, ends = self.groups.get((program, section), ([], [], []))
            i = bisect_left(starts, start + size) - 1
            while i >= 0 and ends[i] > start:
                _, end, claimant = values[i]
                if end > start and claimant != name:
                    return True
                i -= 1
        return False


def eligible(engine, name, owner=None, seed=False, inventory=None, finals=None, history=None, placement_index=None):
    place = engine.place(name)
    names, matched, published, holders = inventory or engine.inventory_state()
    if name in names or name in published or name in (candidates.local_finals(engine) if finals is None else finals):
        return False
    # Every mapped interval counts, including a different C owner of the same bytes.
    wanted = engine.all_places(name)
    if placement_index is not None:
        if name in holders and holders[name] != owner or placement_index.overlaps(wanted, name):
            return False
    else:
        for row in names.values():
            for pr, sec, address, size in row['places']:
                if any(p[0] == pr and p[1] == sec and p[2] < address + size and address < p[2] + p[3] for p in wanted):
                    return False
        for other, holder in holders.items():
            if other == name:
                if holder != owner:
                    return False
            elif other in engine.places and any(p[0] == q[0] and p[1] == q[1] and p[2] < q[2] + q[3] and q[2] < p[2] + p[3]
                                                for p in wanted for q in engine.all_places(other)):
                return False
    if name in engine.remnants or place[4] == 'remnant':
        return False
    if not seed and (engine.root / 'build/studio' / name / 'draft.c').exists():
        return False
    if not seed and name in (previous_jobs(engine) if history is None else history):
        return False
    return True


def public_aliases(engine):
    """Only actual overlapping placements establish local conflict aliases."""
    cached = getattr(engine, '_public_aliases', None)
    if cached is not None:
        return cached
    groups, aliases = {}, {}
    for name in engine.places:
        for program, section, start, size in engine.all_places(name):
            groups.setdefault((program, section), []).append((start, start + size, name))
    for values in groups.values():
        active = []
        for start, end, name in sorted(values):
            active = [(previous_end, previous_name) for previous_end, previous_name in active if previous_end > start]
            for previous_end, previous_name in active:
                if previous_name != name:
                    aliases.setdefault(name, set()).add(previous_name)
                    aliases.setdefault(previous_name, set()).add(name)
            active.append((end, name))
    engine._public_aliases = {n: sorted(values) for n, values in aliases.items()}
    return engine._public_aliases


def reservation_service(engine):
    import project_coordination
    return project_coordination


def public_blocked(engine, names=None, owner=None, progress=None, *, discovery=False):
    return reservation_service(engine).blocked(engine, names, owner, progress, discovery=discovery)


def assert_public_free(engine, names, owner=None):
    reservation_service(engine).assert_free(engine, names, owner)


def launch_names(manager, data):
    """Completed/handed-off cases are not part of a resumed compiler queue."""
    folder = manager.folder(data['id'])
    return [name for name in data.get('nextRunNames', data['names'])
            if read(folder / 'jobs' / name / 'job.json').get('status') == 'queued']


def plan(engine, limit=DEFAULT_LOAD_LIMIT, scope='both', progress=None, *, settings=None, exclude=()):
    size_range = function_size.settings(settings)
    excluded = set(exclude)
    if scope not in ('both', 'main', 'overlays'):
        raise ValueError('Unbekannter Bereich.')
    if type(limit) is not int or limit < 1:
        raise ValueError('Zum Laden eine positive ganze Zahl eingeben.')
    if progress:
        progress(dict(stage='inventory', checked=0))
    publicly_blocked = public_blocked(engine, progress=progress, discovery=True)
    inventory, finals = engine.inventory_state(), candidates.local_finals(engine)
    placement_index = PlacementIndex(engine, inventory)
    # Reject mapped/claimed/unsupported candidates before opening their history.
    # This list is still read-only; a search start revalidates and claims afresh.
    possible = set()
    total = len(engine.places)
    for checked, (name, p) in enumerate(engine.places.items(), 1):
        in_scope = (scope == 'both' or scope == 'main' and p[0] == engine.program
                    or scope == 'overlays' and p[0] != engine.program)
        if (name not in excluded and name not in publicly_blocked and in_scope and function_size.contains(p[3], size_range) and eligible(engine, name, inventory=inventory, finals=finals,
                                                     history=set(), placement_index=placement_index)):
            possible.add(name)
        if progress and (checked % 500 == 0 or checked == total):
            progress(dict(stage='filter', checked=checked, total=total, possible=len(possible)))
    history = previous_jobs(engine, possible, progress=progress)
    possible.difference_update(history)
    if not possible:
        if progress:
            progress(dict(stage='complete', checked=total, found=0))
        return []
    cached = set()
    structured = set()
    environment = candidates.environment(engine)
    for p in (engine.root / 'build/studio/buildable').glob('*.json'):
        try:
            record = read(p)
            if record.get('buildable') and record.get('environment') == environment:
                cached.add(p.stem)
                if re.search(r'\b(?:if|for|while)\b', code_only(record.get('rough', {}).get('source', ''))):
                    structured.add(p.stem)
        except (OSError, ValueError):
            pass
    result = []
    for name, p in sorted(engine.places.items(), key=lambda row: (row[0] not in structured, row[0] not in cached,
                         row[0] not in engine.contracts, not 28 <= row[1][3] <= 320, row[1][3], row[0])):
        if name not in possible:
            continue
        raw = engine.check.disc_bytes(p[:4])
        if not complete_cfg(raw, p[2]) or not candidate_preflight.inspect(raw, p[2])['eligible']:
            continue
        result.append({'name': name, 'size': p[3], 'program': p[0], 'cached': name in cached})
        if progress and len(result) % 100 == 0:
            progress(dict(stage='candidates', found=len(result), requested=limit))
        if len(result) >= limit:
            break
    if progress:
        progress(dict(stage='complete', checked=total, found=len(result)))
    return result


def pin_inputs(engine, names):
    return engine.pin_inputs(names)


def inputs_ok(pins, full=False):
    for filename, pin in pins.items():
        p = Path(filename)
        if not p.is_file() or p.stat().st_size != pin['size'] or p.stat().st_mtime_ns != pin['mtime'] or full and sha(p) != pin['sha256']:
            return False
    return True


def assert_application_runtime(data):
    pinned = {Path(p).resolve().parent for p in data['pins'] if Path(p).name == 'auto_jobs.py'}
    if pinned and pinned != {Path(__file__).resolve().parent}:
        raise ValueError('Sitzung verwendet eine andere Add-on-Version. Vor dem Fortsetzen einen dokumentierten Versionswechsel durchführen.')


def review18_children(source, variants, engine, name, place, raw):
    """Bounded proven rules share the normal history and search budgets."""
    return terminal_scalar_parameter.children(source, variants)


def review19_children(source, variants, engine, name, place, raw):
    """One semantics-preserving store swap on the verified 32-bit EE target.

    Native ordering is not used to infer independence: the AST must establish
    disjoint ranges and private values. The fixed EE pipeline uses 32-bit
    pointers; as with word_store_children, validate its current retail ELF.
    """
    try:
        header = (engine.root / 'include/common.h').read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return []
    children = disjoint_store_swap.children(source, variants, address_bits=32, common_header=header)
    if not children:
        return []
    placements = engine.all_places(name)
    if not placements or tuple(place[:4]) not in {tuple(p[:4]) for p in placements}:
        return []
    engine.assert_reference(place[0])
    if raw != engine.check.disc_bytes(tuple(place[:4])):
        return []
    return children


def reviewed_local_children(source, variants, engine, name, place, raw):
    """Guarded shared C rules and an optional adapter-owned evidence hook."""
    yield from scalar_definition_retarget.children(source, variants)
    yield from postcall_integer_default.generate(source, engine.root)
    adapter_children = getattr(engine, 'additional_search_children', None)
    if callable(adapter_children):
        yield from adapter_children(name, source, variants)


def native_guard_children(source, engine, name, place, raw):
    """A native-read recovery must be supported identically at every placement."""
    children = list(native_guard_variants.generate(source, engine.root, raw, place[2]))
    if not children:
        return []
    placements = engine.all_places(name)
    if not placements or tuple(place[:4]) not in {tuple(p[:4]) for p in placements}:
        return []
    for other in placements:
        engine.assert_reference(other[0])
        other_raw = raw if tuple(other[:4]) == tuple(place[:4]) else engine.check.disc_bytes(other[:4])
        if list(native_guard_variants.generate(source, engine.root, other_raw, other[2])) != children:
            return []
    return children


def native_clear_children(source, engine, name, place, raw):
    """The recovery must be justified by every current retail copy."""
    placements = engine.all_places(name)
    if not placements or tuple(place[:4]) not in {tuple(p[:4]) for p in placements}:
        return []
    evidence = []
    for other in placements:
        engine.assert_reference(other[0])
        body = raw if tuple(other[:4]) == tuple(place[:4]) else engine.check.disc_bytes(other[:4])
        evidence.append((body, other[2]))
    return native_load_before_clear.generate_all(source, engine.root, evidence)


def word_base_children(source, engine, name, place, raw):
    placements = engine.all_places(name)
    if not placements or tuple(place[:4]) not in {tuple(p[:4]) for p in placements}:
        return []
    bodies = []
    for other in placements:
        engine.assert_reference(other[0])
        bodies.append(raw if tuple(other[:4]) == tuple(place[:4]) else engine.check.disc_bytes(other[:4]))
    return list(word_base_variants.generate(source, engine.root, tuple(bodies)))


def word_store_children(source, engine, name, place, raw):
    # The rule only accepts an ID[ID] assignment. Avoid hashing every large
    # retail ELF for the many unrelated cast-based m2c candidates.
    if not re.search(r'\b[A-Za-z_]\w*\s*\[\s*[A-Za-z_]\w*\s*\]\s*=(?!=)', code_only(source)):
        return []
    placements = engine.all_places(name)
    if not placements or tuple(place[:4]) not in {tuple(p[:4]) for p in placements}:
        return []
    bodies = []
    for other in placements:
        # Validates each pinned retail ELF as little-endian 32-bit MIPS.
        engine.assert_reference(other[0])
        bodies.append(raw if tuple(other[:4]) == tuple(place[:4]) else engine.check.disc_bytes(other[:4]))
    return list(word_store_address_variants.generate(source, engine.root, tuple(bodies), address_bits=32))


def native_profile_children(source, engine, name, place, raw):
    if engine.config.get('adapter') != 'RAC4_PAL':
        return []  # Historical profile has no portable ABI proof.

    # Reject unrelated frames before reading/hashing the remaining retail files
    # or probing the existing compiler. The alternative uses the normal budget.
    if native_sd16_profile.native_shape(raw, place[2]) is None:
        return []
    placements = engine.all_places(name)
    if not placements or tuple(place[:4]) not in {tuple(p[:4]) for p in placements}:
        return []
    bodies = []
    for other in placements:
        engine.assert_reference(other[0])
        body = raw if tuple(other[:4]) == tuple(place[:4]) else engine.check.disc_bytes(other[:4])
        bodies.append((body, other[2]))
    if not native_sd16_profile.eligible(source, engine.root, bodies):
        return []
    return list(native_sd16_profile.generate(source, engine.root, bodies,
                status=native_sd16_profile.inspect(engine.root)))


def object_key(path, engine):
    import io
    from elftools.elf.relocation import RelocationSection
    elf = engine.ELFFile(io.BytesIO(Path(path).read_bytes()))
    parts = [(s.name, int(s['sh_flags']), int(s['sh_addralign']), s.data().hex()) for s in elf.iter_sections() if s['sh_flags'] & 2 and s['sh_size']]
    for s in elf.iter_sections():
        if isinstance(s, RelocationSection):
            symbols = elf.get_section(s['sh_link'])
            parts.append((s.name, [(r['r_offset'], r['r_info_type'], symbols.get_symbol(r['r_info_sym']).name,
                                    symbols.get_symbol(r['r_info_sym'])['st_value']) for r in s.iter_relocations()]))
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def interrupted_build(result, record=None):
    diagnostic = (' '.join(str(v) for v in ((record or {}).get('verdict', ''), result.get('verdict', ''),
                                          result.get('log', '')))).replace('\x00', '').lower()
    return any(marker in diagnostic for marker in (
        'zeitlimit', 'timeout', 'timed out', 'e_accessdenied', 'access denied',
        'permission denied', 'zugriff verweigert', 'wsl/', 'failed to start',
        'cannot execute', 'exec format', 'no such file', 'not found',
        'internal compiler error', 'cannot allocate memory', 'memory exhausted'))


def definite_source_error(result):
    diagnostic = str(result.get('log', '')).replace('\x00', '').lower()
    return result.get('verdict') == 'Compilerfehler' and bool(re.search(
        r'(?:\.c:\d+(?::\d+)?:[^\n]*(?:error|undeclared|conflicting)|\b(?:parse|syntax) error\b)', diagnostic))


def reusable_attempts(folder, data, name):
    """Reuse immutable comparisons only with identical compiler/reference/checker inputs."""
    def compile_inputs(pins):
        # Frozen reviewed roots are additional SEARCH inputs, not compiler or
        # retail dependencies. Keep their complete pins in the session so
        # inputs_ok still detects changes, but do not let a fresh session UUID
        # invalidate unrelated historical compiler evidence. Only this exact
        # manager-owned layout and these five known files are projected out.
        session_folder = Path(folder).resolve()
        repository = session_folder.parent.parent.parent
        automatic = repository / 'build/autodecomp'
        frozen_layout = (session_folder.parent == automatic
                         and bool(ID.fullmatch(session_folder.name)))
        frozen_files = {'candidate.c', 'candidate.o', 'result.json', 'checks.json', 'entry.json'}
        result = {}
        for filename, pin in pins.items():
            p = Path(filename)
            if frozen_layout:
                try:
                    relative = p.resolve().relative_to(automatic).parts
                except ValueError:
                    relative = ()
                if (len(relative) == 5 and ID.fullmatch(relative[0])
                        and relative[1] == 'jobs' and FUNCTION.fullmatch(relative[2])
                        and relative[3] == 'review-root' and relative[4] in frozen_files):
                    continue
            # Keep complete paths as well as every shared hash. Inferring an
            # app root from a basename can collide with a project's own tools.
            result[filename] = pin['sha256']
        return result
    expected = compile_inputs(data['pins']); found = {}
    for archive in [folder, *reversed(data.get('history', []))]:
        archive = Path(archive)
        # Resume can traverse its own immutable comparisons without repeating
        # compiler work. Other evidence must be a manager-owned pass archive.
        current = archive.resolve() == folder.resolve()
        if not current and archive.parent.resolve() != (folder / 'history').resolve():
            continue
        try:
            origin = data if current else read(archive / 'session.json')
            if compile_inputs(origin['pins']) != expected:
                continue
            accounting = read(archive / 'jobs' / name / 'job.json').get('timeAccounting')
            if (not isinstance(accounting, dict) or type(accounting.get('version')) is not int
                    or accounting['version'] != search_time.VERSION
                    or accounting.get('inputs') != search_time.input_identity(origin['pins'])):
                continue  # A selective retry can also archive another job's old pass under newer session pins.
        except (OSError, ValueError, KeyError, TypeError):
            continue
        log = archive / 'jobs' / name / 'attempts.jsonl'
        if not log.is_file():
            continue
        for line in log.read_text(encoding='utf-8').splitlines():
            try:
                record = json.loads(line); snapshot = Path(record['snapshot'])
                if sha(snapshot) != record['sourceHash']:
                    continue
                result = read(snapshot.with_name('result.json'))
                if result['snapshot'] != str(snapshot) or result['sourceHash'] != record['sourceHash']:
                    continue
                if interrupted_build(result, record) or ('compiledSize' not in result and not definite_source_error(result)):
                    continue
                obj = snapshot.with_suffix('.o')
                if 'compiledSize' in result and (not record.get('objectHash') or sha(obj) != record['objectHash']):
                    continue
                cached = dict(result, _cacheObjectHash=record.get('objectHash'),
                              _cacheResultHash=sha(snapshot.with_name('result.json')))
                found.setdefault(source_key(snapshot.read_text(encoding='utf-8')), cached)
            except (OSError, ValueError, KeyError):
                continue
    return found


def same_cached_source(result, source):
    """A search key alone is never evidence that this exact C was compiled."""
    try:
        snapshot = Path(result['snapshot'])
        if sha(snapshot) != result['sourceHash'] or snapshot.read_text(encoding='utf-8') != source:
            return False
        if sha(snapshot.with_name('result.json')) != result['_cacheResultHash']:
            return False
        if 'compiledSize' in result and sha(snapshot.with_suffix('.o')) != result['_cacheObjectHash']:
            return False
        return True
    except (OSError, KeyError, ValueError):
        return False


def prior_failed_sources(folder, data, name):
    """Negative search history is not compiler evidence for the new runtime."""
    found = set()
    root=Path(folder).parent.parent.parent
    try:
        retired=retention.load(root,name).get('retired',{})
    except (OSError,ValueError,KeyError,TypeError):
        retired={}
    for archive in data.get('history', []):
        archive = Path(archive)
        if archive.parent.resolve() != (folder / 'history').resolve():
            continue
        log = archive / 'jobs' / name / 'attempts.jsonl'
        if not log.is_file():
            continue
        for line in log.read_text(encoding='utf-8').splitlines():
            try:
                record = json.loads(line); snapshot = Path(record['snapshot'])
                if not snapshot.exists():
                    key=retention.retired_key(root,name,record,retired)
                    if key and not record.get('technicalMatch'):
                        found.add(key)
                    continue
                if record.get('technicalMatch') or sha(snapshot) != record['sourceHash']:
                    continue
                result = read(snapshot.with_name('result.json'))
                if result['sourceHash'] != record['sourceHash'] or result['snapshot'] != str(snapshot):
                    continue
                # Exhausted wall time and host/WSL failures say nothing about C.
                # In particular, a final attempt may have had only1--2s left.
                if interrupted_build(result, record):
                    continue
                compiled = isinstance(result.get('compiledSize'), int) and result['compiledSize'] > 0
                if compiled:
                    if not record.get('objectHash') or sha(snapshot.with_suffix('.o')) != record['objectHash']:
                        continue
                else:
                    # Only definite source diagnostics, not a generic nonzero
                    # compiler/tool-host exit, establish a failed source variant.
                    if not definite_source_error(result):
                        continue
                found.add(source_key(snapshot.read_text(encoding='utf-8')))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return found


class Manager:
    def __init__(self, repository):
        self.root = Path(repository).resolve()
        self.directory = self.root / 'build/autodecomp'
        self._engine = None
        if (self.root / '.git').exists():
            admin = self.root / '.git'
            if admin.is_file():
                prefix, value = admin.read_text(encoding='utf-8').strip().split(':', 1)
                if prefix != 'gitdir':
                    raise ValueError('Unbekannte Worktree-Gitverknüpfung.')
                admin = (self.root / value.strip()).resolve()
            shared = admin / 'commondir'
            common = (admin / shared.read_text(encoding='utf-8').strip()).resolve() if shared.exists() else admin.resolve()
            expected = common.parent / 'build/claims'
            if expected.resolve() != (self.root / 'build/claims').resolve():
                raise ValueError('Dieser Worktree verwendet keinen gemeinsamen Claim-Ordner. Vor paralleler Arbeit build/claims mit dem Hauptrepository verbinden.')

    @property
    def engine(self):
        if self._engine is None:
            self._engine = AutoEngine(self.root)
        return self._engine

    def folder(self, session):
        if not isinstance(session, str) or not ID.fullmatch(session):
            raise ValueError('Ungültige Sitzung.')
        folder = self.directory / session
        if folder.resolve() != self.directory.resolve() / session:
            raise ValueError('Sitzungsordner darf keine umgeleitete Verknüpfung sein.')
        return folder

    def create(self, names, settings=None, seeds=None):
        if not isinstance(names, list) or not names or len(set(names)) != len(names):
            raise ValueError('Mindestens eine eindeutige Funktion auswählen; keine Duplikate.')
        settings = options(settings)
        if any(not function_size.contains(self.engine.place(name)[3], settings) for name in names):
            raise ValueError('Auswahl liegt außerhalb des eingestellten Byte-Bereichs. Funktionen neu laden.')
        assert_public_free(self.engine, names)
        seeds = seeds or {}
        if any(not isinstance(v, str) or not v.strip() or len(v) > 256000 for v in seeds.values()) or set(seeds) - set(names):
            raise ValueError('Ungültiger Startentwurf.')
        session = uuid.uuid4().hex
        folder = self.folder(session)
        owner = 'ps2decomp-auto-' + session
        claims = claim_api(self.engine)
        acquired, skipped = [], []
        folder.mkdir(parents=True)
        try:
            # No jobs run during reservation. Reuse inventory while acquiring,
            # then validate the whole batch against a fresh snapshot before launch.
            inventory = self.engine.inventory_state()
            finals, history = candidates.local_finals(self.engine), previous_jobs(self.engine)
            for name in names:
                if not eligible(self.engine, name, seed=name in seeds, inventory=inventory, finals=finals, history=history):
                    skipped.append({'name': name, 'reason': 'Bereits bearbeitet, reserviert oder eigener Editorentwurf.'})
                    continue
                if not claims.claim(owner, name):
                    skipped.append({'name': name, 'reason': 'Zwischenzeitlich von einem Agenten reserviert.'})
                    continue
                acquired.append(name)
                p = self.engine.place(name)
                raw = self.engine.check.disc_bytes(p[:4]); screen = candidate_preflight.inspect(raw, p[2])
                if not complete_cfg(raw, p[2]) or not screen['eligible']:
                    claims.release(owner, name); acquired.remove(name)
                    skipped.append({'name': name, 'reason': '; '.join(screen['reasons']) or 'Keine vollständige unterstützte Funktionsgrenze.'})
                    continue
                inventory[3][name] = owner
            inventory = self.engine.inventory_state()
            finals, history = candidates.local_finals(self.engine), previous_jobs(self.engine)
            for name in list(acquired):
                if (inventory[3].get(name) != owner or
                        not eligible(self.engine, name, owner, seed=name in seeds, inventory=inventory, finals=finals, history=history)):
                    claims.release(owner, name); acquired.remove(name)
                    skipped.append({'name': name, 'reason': 'Auswahl hat sich während der Reservierung geändert.'})
            if not acquired:
                raise ValueError('Keine freie Funktion übrig. Bestehende Agentenreservierungen werden nicht übernommen.')
            pins = pin_inputs(self.engine, acquired)
            data = dict(id=session, owner=owner, created=now(), status='queued', options=settings, names=acquired,
                        skipped=skipped, pins=pins, runner=None, review=str(folder / 'REVIEW.md'), version=getattr(self.engine, 'version', 'pal'))
            for name in acquired:
                jobdir = folder / 'jobs' / name
                jobdir.mkdir(parents=True)
                if name in seeds:
                    (jobdir / 'seed.c').write_text(seeds[name], encoding='utf-8')
                write(jobdir / 'job.json', dict(name=name, status='queued', attempts=0, best=None, created=now(),
                      inputMode='editor' if name in seeds else 'automatic', timeAccounting=search_time.initial(settings, pins)))
            write(folder / 'session.json', data)
            self.export(session)
            return self.status(session)
        except BaseException:
            for name in acquired:
                claims.release(owner, name)
            raise

    def status(self, session):
        folder = self.folder(session); data = read(folder / 'session.json')
        data = {k: v for k, v in data.items() if k != 'pins'}
        data['jobs'] = [read(folder / 'jobs' / n / 'job.json') for n in data['names']]
        for job in data['jobs']:
            job['deferred'] = bool(is_deferred(job))
        data['deferredCount'] = sum(j['deferred'] for j in data['jobs'])
        runner = data.get('runner') or {}
        launch = read(folder / 'launch.json') if (folder / 'launch.json').is_file() else {}
        data['alive'] = (data['status'] == 'running' and live_process(runner.get('pid'), runner.get('birth'))
                         or data['status'] in ('queued', 'running') and live_process(launch.get('pid'), launch.get('birth')))
        if data['status'] == 'running' and not data['alive']:
            data['status'] = 'interrupted'
        claims = claim_api(self.engine)
        data['reserved'] = [j['name'] for j in data['jobs'] if claims.holder(j['name']) == data['owner']]
        data['stopRequested'] = (folder / 'stop.request').exists()
        return data

    def _latest_jobs(self):
        """A newer reservation or unreadable latest job must hide older handoffs."""
        sessions = []
        for path in self.directory.glob('*/session.json'):
            try:
                data = read(path)
                if data['id'] != path.parent.name or not ID.fullmatch(data['id']):
                    continue
                sessions.append((data['created'], data['id'], data, path))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        latest = {}
        for _, _, data, path in sorted(sessions, key=lambda r: (r[0], r[1]), reverse=True):
            for name in data.get('names', []):
                latest.setdefault(name, (data, path))
        return latest

    def _retry_entry(self, name, latest, inventory, finals, owner=None, placement_index=None, size_range=None,
                     *, include_unclaimed=False, history=None):
        if not isinstance(name, str) or not FUNCTION.fullmatch(name):
            raise ValueError('Ungültiger Funktionsname.')
        if name not in latest:
            if not include_unclaimed:
                raise ValueError('Kein früherer Suchlauf: ' + name)
            if (claim_api(self.engine).holder(name) != owner or
                    not eligible(self.engine, name, owner, inventory=inventory, finals=finals,
                                 history=history, placement_index=placement_index)):
                raise ValueError('Funktion ist zugeordnet, reserviert oder besitzt einen Editorentwurf: ' + name)
            place = self.engine.place(name)
            if not function_size.contains(place[3], size_range):
                raise ValueError('Funktionsgröße wird nicht unterstützt: ' + name)
            for p in self.engine.all_places(name):
                raw = self.engine.check.disc_bytes(p)
                if not complete_cfg(raw, p[2]) or not candidate_preflight.inspect(raw, p[2])['eligible']:
                    raise ValueError('Funktionsgrenze oder Vorprüfung ungeeignet: ' + name)
            return dict(data=None, job=None, source=None, sessionHash=None, jobHash=None)
        data, session_path = latest[name]
        folder = self.folder(data['id']); jobpath = folder / 'jobs' / name / 'job.json'
        job = read(jobpath)
        if job.get('name') != name or job.get('status') != 'handed_off' or not job.get('handedOff'):
            raise ValueError('Neuester Fall wurde nicht ausdrücklich an Review freigegeben: ' + name)
        # Only the history exclusion is bypassed. Editor drafts, every interval,
        # canonical/local finals and all foreign claims retain their normal gates.
        if (claim_api(self.engine).holder(name) != owner or
                not eligible(self.engine, name, owner, inventory=inventory, finals=finals, history=set(), placement_index=placement_index)):
            raise ValueError('Funktion ist zugeordnet, reserviert oder besitzt einen Editorentwurf: ' + name)
        place = self.engine.place(name)
        if not function_size.contains(place[3], size_range):
            raise ValueError('Funktionsgröße wird nicht unterstützt: ' + name)
        for p in self.engine.all_places(name):
            raw = self.engine.check.disc_bytes(p)
            if not complete_cfg(raw, p[2]) or not candidate_preflight.inspect(raw, p[2])['eligible']:
                raise ValueError('Funktionsgrenze oder Vorprüfung ungeeignet: ' + name)
        best = job.get('best')
        if (best or job.get('lastAttempt') or {}).get('technicalMatch'):
            raise ValueError('Technischer Treffer gehört in die Review: ' + name)
        source = None
        if best:
            snapshot = Path(best['snapshot'])
            if sha(snapshot) != best.get('sourceHash'):
                raise ValueError('Bester Snapshot verändert: ' + name)
            result = read(snapshot.with_name('result.json'))
            obj = snapshot.with_suffix('.o')
            if (result.get('name') != name or result.get('snapshot') != str(snapshot) or result.get('sourceHash') != best['sourceHash'] or
                    not isinstance(result.get('compiledSize'), int) or result['compiledSize'] <= 0 or
                    not best.get('objectHash') or sha(obj) != best['objectHash']):
                raise ValueError('Compilerbeleg des besten Snapshots fehlt oder wurde verändert: ' + name)
            if result.get('automaticStrict', {}).get('passed'):
                raise ValueError('Technischer Treffer gehört in die Review: ' + name)
            source = snapshot.read_bytes()
            source.decode('utf-8')  # A corrupt best is not silently replaced by a fresh guess.
        return dict(data=data, job=job, sessionPath=session_path, jobpath=jobpath,
                    sessionHash=sha(session_path), jobHash=sha(jobpath), source=source)

    def retry_plan(self, limit=DEFAULT_LOAD_LIMIT, scope='incomplete', program_scope='both', *, settings=None):
        """Read-only, explicitly handed-off unfinished results; never claim here."""
        size_range = function_size.settings(settings)
        if type(limit) is not int or limit < 1:
            raise ValueError('Zum Laden eine positive ganze Zahl eingeben.')
        if scope not in ('incomplete', 'all') or program_scope not in ('both', 'main', 'overlays'):
            raise ValueError('Unbekannter Wiederholungsbereich.')
        publicly_blocked = public_blocked(self.engine, discovery=True)
        latest = self._latest_jobs(); inventory = self.engine.inventory_state()
        placement_index = PlacementIndex(self.engine, inventory)
        finals = candidates.local_finals(self.engine); rows = []
        for name in latest:
            if name in publicly_blocked:
                continue
            try:
                entry = self._retry_entry(name, latest, inventory, finals, placement_index=placement_index, size_range=size_range)
                data, job = entry['data'], entry['job']; p = self.engine.place(name)
                analysis_required = bool(is_deferred(job) or not job.get('best'))
                if scope == 'incomplete' and analysis_required:
                    continue
                if program_scope == 'main' and p[0] != self.engine.program or program_scope == 'overlays' and p[0] == self.engine.program:
                    continue
                rows.append(dict(name=name, size=p[3], program=p[0], cached=False,
                                 previousSession=data['id'], previousStatus=job['status'],
                                 deferred=bool(is_deferred(job)), bestScore=(job.get('best') or {}).get('score'),
                                 analysisRequired=analysis_required,
                                 reason=job.get('reason', 'Unvollständiges Ergebnis')))
                if len(rows) >= limit:
                    break
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return rows

    def mixed_plan(self, limit=DEFAULT_LOAD_LIMIT, program_scope='both', *, settings=None, progress=None):
        """Released unfinished results first, then genuinely new unclaimed functions."""
        rows = self.retry_plan(limit, 'all', program_scope, settings=settings)
        rows = [dict(row, candidateKind='retry') for row in rows]
        if len(rows) < limit:
            # A recorded case must pass the handoff/evidence gates above, even
            # if it has zero attempts and ordinary discovery could list it.
            fresh = plan(self.engine, limit - len(rows), program_scope, progress,
                         settings=settings, exclude=self._latest_jobs())
            rows.extend(dict(row, candidateKind='free') for row in fresh)
        return rows

    def create_retry(self, names, settings=None, progress=None, *, include_unclaimed=False):
        """New owner and pins; preserve every byte of the released earlier run."""
        import shutil
        if (not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names) or
                len(set(names)) != len(names)):
            raise ValueError('Mindestens eine eindeutige Funktion auswählen; keine Duplikate.')
        settings = options(settings)
        if any(not function_size.contains(self.engine.place(name)[3], settings) for name in names):
            raise ValueError('Auswahl liegt außerhalb des eingestellten Byte-Bereichs. Funktionen neu laden.')
        assert_public_free(self.engine, names)
        latest = self._latest_jobs()
        def report(stage, done):
            if progress and (done % 100 == 0 or done == len(names)):
                progress(dict(stage=stage, done=done, total=len(names)))
        inventory = self.engine.inventory_state(); finals = candidates.local_finals(self.engine)
        placement_index = PlacementIndex(self.engine, inventory)
        processed = previous_jobs(self.engine, set(names)) if include_unclaimed else None
        entries = {}
        report('validate', 0)
        for i, name in enumerate(names, 1):
            entries[name] = self._retry_entry(name, latest, inventory, finals, placement_index=placement_index,
                                             size_range=settings, include_unclaimed=include_unclaimed, history=processed)
            report('validate', i)
        session = uuid.uuid4().hex; folder = self.folder(session); owner = 'ps2decomp-auto-' + session
        claims = claim_api(self.engine); acquired = []
        try:
            report('reserve', 0)
            for i, name in enumerate(names, 1):
                if not claims.claim(owner, name):
                    raise ValueError('Zwischenzeitlich reserviert: ' + name)
                acquired.append(name)
                report('reserve', i)
            # Re-read the entire selection after claiming, before any session is published.
            latest = self._latest_jobs(); inventory = self.engine.inventory_state()
            finals = candidates.local_finals(self.engine)
            placement_index = PlacementIndex(self.engine, inventory)
            processed = previous_jobs(self.engine, set(names)) if include_unclaimed else None
            report('revalidate', 0)
            for i, name in enumerate(names, 1):
                checked = self._retry_entry(name, latest, inventory, finals, owner, placement_index, size_range=settings,
                                            include_unclaimed=include_unclaimed, history=processed)
                if any(checked[k] != entries[name][k] for k in ('sessionHash', 'jobHash')):
                    raise ValueError('Früheres Ergebnis während der Reservierung verändert: ' + name)
                report('revalidate', i)
            pins = pin_inputs(self.engine, names)
            # Freeze only explicitly exported review candidates into this NEW
            # retry. A running worker never consults a changing review registry.
            registry_path = self.root / 'build/review-search-roots.json'
            try:
                review_registry = read(registry_path) if registry_path.is_file() else None
            except (OSError, ValueError):
                review_registry = {'invalid': True}
            history, archives = [], {}
            report('archive', 0)
            for i, (name, entry) in enumerate(entries.items(), 1):
                data = entry['data']; oldfolder = self.folder(data['id']) if data else None
                origins = [oldfolder] if oldfolder else []
                for old in data.get('history', []) if data else ():
                    old = Path(old)
                    if old.parent.resolve() == (oldfolder / 'history').resolve():
                        origins.append(old)
                for origin in origins:
                    sourcejob = origin / 'jobs' / name
                    if not sourcejob.is_dir():
                        continue
                    key = str(origin.resolve())
                    if key not in archives:
                        archive = folder / 'history' / ('retry-' + uuid.uuid4().hex)
                        archive.mkdir(parents=True); shutil.copy2(origin / 'session.json', archive / 'session.json')
                        archives[key] = archive; history.append(str(archive))
                    shutil.copytree(sourcejob, archives[key] / 'jobs' / name)
                directory = folder / 'jobs' / name; directory.mkdir(parents=True)
                if entry['source'] is not None:
                    (directory / 'seed.c').write_bytes(entry['source'])
                prepared_job = dict(name=name, status='queued', attempts=0, best=None, created=now(),
                      inputMode='retry' if entry['source'] is not None else 'automatic')
                if data:
                    prepared_job.update(previousSession=data['id'], previousAttempts=entry['job'].get('attempts', 0),
                        retryBest=entry['job'].get('best'), retryDeferred=bool(is_deferred(entry['job'])))
                if review_registry is not None and data:
                    try:
                        reviewed = reviewed_seeds.capture(self.root, name, directory, review_registry)
                        if reviewed:
                            prepared_job['reviewedSeed'] = reviewed
                            for filename, digest in reviewed.get('files', {}).items():
                                path = Path(filename)
                                if sha(path) != digest:
                                    raise ValueError('Review-Suchbasis beim Einfrieren veraendert.')
                                pins[str(path.resolve())] = dict(sha256=digest, size=path.stat().st_size,
                                                               mtime=path.stat().st_mtime_ns)
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        prepared_job['reviewedSeed'] = dict(state='rejected', reason=str(exc)[:800])
                write(directory / 'job.json', prepared_job)
                report('archive', i)
            # Frozen reviewed roots can extend the session pins while preparing
            # later jobs. Bind every new pass only after that complete capture.
            for name in names:
                jobpath = folder / 'jobs' / name / 'job.json'
                job = read(jobpath); job['timeAccounting'] = search_time.initial(settings, pins)
                write(jobpath, job)
            # Hash recheck prevents a changed best from passing via a retained byte copy.
            latest = self._latest_jobs(); inventory = self.engine.inventory_state()
            finals = candidates.local_finals(self.engine)
            placement_index = PlacementIndex(self.engine, inventory)
            processed = previous_jobs(self.engine, set(names)) if include_unclaimed else None
            report('final', 0)
            for i, name in enumerate(names, 1):
                checked = self._retry_entry(name, latest, inventory, finals, owner, placement_index, size_range=settings,
                                            include_unclaimed=include_unclaimed, history=processed)
                if any(checked[k] != entries[name][k] for k in ('sessionHash', 'jobHash')):
                    raise ValueError('Früheres Ergebnis während der Archivierung verändert: ' + name)
                report('final', i)
            data = dict(id=session, owner=owner, created=now(), status='queued', options=settings, names=names,
                        skipped=[], pins=pins, runner=None, review=str(folder / 'REVIEW.md'), history=history,
                        purpose='unclaimed-and-open' if include_unclaimed else 'retry-incomplete', version=self.engine.version,
                        retrySources={n: e['data']['id'] for n, e in entries.items() if e['data']})
            write(folder / 'session.json', data); self.export(session)
            return self.status(session)
        except BaseException:
            for name in acquired:
                claims.release(owner, name)
            if folder.exists():
                # Only our fresh, validated UUID beneath this manager's directory.
                if folder.resolve().parent == self.directory.resolve():
                    shutil.rmtree(folder)
            raise

    def sessions(self):
        result = []
        for path in sorted(self.directory.glob('*/session.json'), reverse=True):
            try:
                data = read(path)
                runner = data.get('runner') or {}
                launch = read(path.parent/'launch.json') if (path.parent/'launch.json').is_file() else {}
                alive = (data['status'] == 'running' and live_process(runner.get('pid'), runner.get('birth'))
                         or data['status'] in ('queued', 'running') and live_process(launch.get('pid'), launch.get('birth')))
                result.append(dict(id=data['id'], created=data['created'], purpose=data.get('purpose'),
                                   status='interrupted' if data['status']=='running' and not alive else data['status'],
                                   functionCount=len(data['names']), alive=bool(alive)))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(result, key=lambda d: d['created'], reverse=True)[:20]

    def deferred(self):
        """Read-only later-work inventory, including sessions outside the latest20."""
        sessions = []
        for path in self.directory.glob('*/session.json'):
            try:
                sessions.append(read(path))
            except (OSError, ValueError):
                continue
        inventory = self.engine.inventory_state()
        completed = set(inventory[0]) | set(inventory[2]) | candidates.local_finals(self.engine)
        seen, result = set(), []
        for data in sorted(sessions, key=lambda s: s['created'], reverse=True):
            for name in data['names']:
                if name in seen:
                    continue
                seen.add(name)
                try:
                    job = read(self.folder(data['id']) / 'jobs' / name / 'job.json')
                except (OSError, ValueError):
                    continue
                if name in completed or not is_deferred(job):
                    continue
                result.append(dict(name=name, session=data['id'], created=data['created'],
                                   reason=job.get('reason', 'Weitere Analyse erforderlich'),
                                   status=job['status'], owner=inventory[3].get(name), review=data['review']))
        return result

    def start(self, session):
        folder = self.folder(session)
        with file_lock(self.root / 'build/retention/control.guard'), file_lock(folder / 'launch.guard'):
            data = read(folder / 'session.json')
            if self.status(session)['alive']:
                return self.status(session)
            assert_application_runtime(data)
            if data.get('version') != self.engine.version:
                raise ValueError('Diese Spielversion wird von diesem Prüfkern nicht unterstützt.')
            assert_public_free(self.engine, launch_names(self, data), data['owner'])
            retention.refresh_protection(self.root)
            if data['status'] in ('paused', 'error', 'running'):
                (folder / 'stop.request').unlink(missing_ok=True)
            with (folder / 'runner.log').open('ab') as log:
                child = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()), 'run', '--repo', str(self.root), '--session', session],
                                         cwd=self.root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            write(folder / 'launch.json', {'pid': child.pid, 'birth': process_birth(child.pid), 'started': now()})
            return dict(self.status(session), launchedPid=child.pid)

    def stop(self, session):
        (self.folder(session) / 'stop.request').write_text(now())
        return self.status(session)

    def checks(self, session, name):
        """Generate read-only evidence for exactly the saved candidate, no builds."""
        folder=self.folder(session);data=read(folder/'session.json')
        if name not in data['names']:
            raise ValueError('Funktion gehört nicht zur Sitzung.')
        job=read(folder/'jobs'/name/'job.json')
        candidate=job.get('best') or job.get('lastAttempt') or job.get('roughCandidate')
        from review_checks import write_report
        return write_report(self.engine,name,candidate)

    def retry(self, session, deferred_only=False):
        """Explicit new search pass; keep ownership and a complete previous-pass archive."""
        import shutil
        folder = self.folder(session)
        with file_lock(folder / 'launch.guard'), file_lock(folder / 'runner.guard'):
            data = read(folder / 'session.json')
            if self.status(session)['alive']:
                raise ValueError('Zuerst den laufenden Suchprozess beenden.')
            claims = claim_api(self.engine)
            names = []
            for name in data['names']:
                job = read(folder / 'jobs' / name / 'job.json')
                if job['status'] == 'handed_off' or (job.get('best') or {}).get('technicalMatch'):
                    continue
                if bool(is_deferred(job)) != deferred_only:
                    continue
                if claims.holder(name) != data['owner'] or not eligible(self.engine, name, data['owner'], seed=True):
                    raise ValueError('Reservierung oder Zuordnung geändert: ' + name)
                for key in ('best', 'lastAttempt', 'roughCandidate'):
                    candidate = job.get(key)
                    if candidate and sha(candidate['snapshot']) != candidate['sourceHash']:
                        raise ValueError('Vorheriger Snapshot verändert: ' + name)
                names.append(name)
            if not names:
                raise ValueError('Keine reservierten zurückgestellten Fälle.' if deferred_only else
                                 'Keine anderen offenen Ergebnisse. Zurückgestellte Fälle werden zunächst ausgelassen.')
            # Other queued jobs may later resume after this selected pass.
            # Retain reference coverage for every function in the session.
            pins = pin_inputs(self.engine, data['names'])
            archive = folder / 'history' / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
            archive.mkdir(parents=True)
            # Copy before mutating any current result. Old paths remain verifiable.
            shutil.copy2(folder / 'session.json', archive / 'session.json')
            shutil.copytree(folder / 'jobs', archive / 'jobs')
            shutil.copy2(folder / 'REVIEW.md', archive / 'REVIEW.md')
            for name in names:
                directory = folder / 'jobs' / name
                job = read(directory / 'job.json')
                source = directory / 'seed.c'
                provenance = read(directory / 'rough.json') if (directory / 'rough.json').is_file() else {}
                refresh = bool(source.exists() and provenance.get('source') == source.read_text(encoding='utf-8')
                               and provenance.get('adapterVersion') != rough.ADAPTER_VERSION)
                if not source.exists() and job.get('roughCandidate'):
                    source.write_text(Path(job['roughCandidate']['snapshot']).read_text(encoding='utf-8'), encoding='utf-8')
                write(directory / 'job.json', dict(name=name, status='queued', attempts=0, best=None, created=now(),
                                                   timeAccounting=search_time.initial(data['options'], pins),
                                                   previousPass=str(archive / 'jobs' / name / 'job.json'),
                                                   previousAttempts=job.get('previousAttempts', 0) + job.get('attempts', 0),
                                                   inputMode=job.get('inputMode', 'automatic'), refreshDraft=refresh,
                                                   roughCandidate=job.get('roughCandidate') or
                                                   ({'snapshot': str(directory / 'rough.c'), 'sourceHash': sha(directory / 'rough.c')}
                                                    if (directory / 'rough.c').is_file() else None)))
                # Never overwrite the old log: it is archived along with every prior snapshot.
                (directory / 'attempts.jsonl').unlink(missing_ok=True)
            data.update(status='queued', pins=pins, runner=None, nextRunNames=names, searchPass=data.get('searchPass', 1) + 1)
            data.setdefault('history', []).append(str(archive))
            data.pop('error', None)
            (folder / 'stop.request').unlink(missing_ok=True)
            write(folder / 'session.json', data)
            self.export(session)
        return self.start(session)

    def handoff(self, session, name):
        return self.handoff_many(session, [name])

    def handoff_many(self, session, names):
        folder = self.folder(session)
        # Stop/restart and handoff cannot race across windows or CLI processes.
        with file_lock(folder / 'launch.guard'), file_lock(folder / 'runner.guard'):
            data = read(folder / 'session.json')
            if not isinstance(names, list) or not names or len(set(names)) != len(names):
                raise ValueError('Mindestens eine eindeutige Funktion freigeben; keine Duplikate.')
            if any(name not in data['names'] for name in names):
                raise ValueError('Funktion gehört nicht zur Sitzung.')
            if self.status(session)['alive']:
                raise ValueError('Zuerst die Sitzung stoppen und das Ende des aktuellen Compilerlaufs abwarten.')
            claims = claim_api(self.engine)
            jobs = []
            # Validate the entire request before releasing any reservation.
            for name in names:
                jobpath = folder / 'jobs' / name / 'job.json'; job = read(jobpath)
                candidate = job.get('best') or job.get('lastAttempt') or job.get('roughCandidate')
                if candidate and sha(candidate['snapshot']) != candidate['sourceHash']:
                    raise ValueError('Bester Snapshot wurde verändert. Vor Freigabe prüfen.')
                holder = claims.holder(name)
                if holder not in (None, data['owner']) and job['status'] != 'handed_off':
                    raise ValueError('Reservierung gehört inzwischen einem anderen Worker; keine Freigabe durchgeführt.')
                jobs.append((name, jobpath, job))
            for name, jobpath, job in jobs:
                claims.release(data['owner'], name)
                job.update(deferred=bool(is_deferred(job)), status='handed_off', handedOff=now())
                write(jobpath, job)
            self.export(session)
            return self.status(session)

    def export(self, session):
        folder = self.folder(session); data = read(folder / 'session.json')
        lines = ['# PS2DECOMP automatische Suche: Review', '', f'Sitzung: `{session}`', f'Claim-Owner: `{data["owner"]}`', '',
                 'Alle Ergebnisse sind ungeprüfte Kandidaten. Der Suchwert misst Instruktionsähnlichkeit, keine Korrektheit.',
                 'Reservierungen bleiben bis zur ausdrücklichen Übergabe bestehen. Vor weiterer Bearbeitung in der Anwendung freigeben und anschließend selbst atomar claimen.', '',
                 'Zurückgestellte Fälle zunächst auslassen. Sie bleiben mit Abbruchgrund und unveränderten Belegen für spätere gezielte Nacharbeit gespeichert; fehlende Funktionskörper von Aufgerufenen sind kein belegter Blocker.', '',
                 'Lokale manuelle Auswahl: Nur freigegebene Kandidaten ab Suchwert 80 weiterbearbeiten, auch nach früheren automatischen Versuchen. Niedrigere Fälle für spätere automatische Suchläufe zurück in den Pool geben; nur eigene manuelle Claims freigeben. Originale Kandidaten und Versuchshistorien behalten. Dies ist keine Korrektheitsgrenze und gehört nicht in öffentliche Reservierungskommentare.', '',
                 'Zeit- und Versuchslimits des Addons gelten nur für automatische Suchläufe, nicht für manuelle Review oder manuelle Decompilierung. Ein ausgeschöpftes automatisches Budget ist kein Abbruchgrund für die manuelle Nacharbeit. Automatische Einstellungen, Zähler und Versuchshistorien unverändert lassen und manuelle Versuche getrennt protokollieren. Nach Möglichkeit bis zum unabhängig geprüften Match weiterarbeiten oder das konkrete verbleibende Hindernis samt Belegen dokumentieren.', '',
                 'Follow the game project AGENTS.md and coordination policy before new function work. Never take foreign or automatic claims. Public comments need explicit authorization. A search score is not verification.', '',
                 '## Arbeitsauftrag für die Review', '', 'Beste unveränderliche Kandidaten und result.json lesen. Signaturen, Speicherzugriffe, Initialwerte, Datenbesitz, alle tatsächlichen Relokationsziele und alle Platzierungen prüfen. Nur echte neue Finals zählen. Compiler unverändert lassen. Keine Versuche doppelt wiederholen; attempts.jsonl und Suchabbruch beachten.', '']
        for archive in data.get('history', []):
            lines += [f'Vollständiger früherer Suchpass: `{archive}`', '']
        for name in data['names']:
            job = read(folder / 'jobs' / name / 'job.json')
            lines += [f'## {name}', '', f'Status: {job["status"]}; Versuche: {job["attempts"]}; Grund: {job.get("reason", "noch offen")}', '']
            if is_deferred(job):
                lines += ['**Zurückgestellt · später prüfen. Im aktuellen Review zunächst auslassen.**', '']
            if job.get('normalizations'):
                lines += ['Vorbereitung: ' + '; '.join(job['normalizations']), '']
            if job.get('variantNote'):
                lines += ['Suchdiagnose: ' + job['variantNote'], '']
            if job.get('reviewedSeed'):
                reviewed = job['reviewedSeed']
                lines += ['Zusaetzliche offene Review-Suchbasis: ' + reviewed.get('state', 'unbekannt')
                          + '; kein Korrektheitsnachweis. ' + reviewed.get('reason', ''), '']
            if job.get('originalCompiler'):
                lines += ['Originalcompilerprofil: ' + job['originalCompiler']['reason'], '']
            if job.get('checks', {}).get('path'):
                lines += [f'Automatischer Prüfbericht: `{job["checks"]["path"]}`; fachliche Freigabe bleibt manuell.', '']
            if job.get('best'):
                best = job['best']
                lines += [f'Suchwert: {best["score"]:.3f}; technischer Treffer: {best["technicalMatch"]}',
                          f'Beste Quelle: `{best["snapshot"]}`', f'SHA256: `{best["sourceHash"]}`',
                          f'Compiler-/Vergleichsbeleg: `{Path(best["snapshot"]).with_name("result.json")}`', '']
            elif job.get('lastAttempt'):
                attempt = job['lastAttempt']
                lines += [f'Letzter noch nicht kompilierbarer Entwurf: `{attempt["snapshot"]}`',
                          f'Compilerprotokoll: `{Path(attempt["snapshot"]).with_name("compiler.log")}`', '']
            elif job.get('roughCandidate'):
                lines += [f'Unvollständiger Grobentwurf: `{job["roughCandidate"]["snapshot"]}`',
                          f'm2c-Belege: `{folder / "jobs" / name / "rough.json"}`', '']
            lines += [f'Versuchsverlauf: `{folder / "jobs" / name / "attempts.jsonl"}`', '']
        from presentation_language import markdown
        (folder / 'REVIEW.md').write_text(markdown('\n'.join(lines)), encoding='utf-8')


def search_job(manager, session, name):
    folder = manager.folder(session); data = read(folder / 'session.json'); settings = options(data['options'])
    engine = AutoEngine(manager.root, version=data.get('version'))  # Never relabel PAL bytes as another version.
    directory = folder / 'jobs' / name; jobpath = directory / 'job.json'; job = read(jobpath)
    if job.get('status') != 'queued' or is_deferred(job):
        return  # Review/handoff never implicitly restarts a completed or deferred job.
    claims = claim_api(engine)
    try:
        budget = search_time.Budget(job, directory, settings, data['pins'])
    except ValueError as exc:
        # Do not replace legacy elapsed/started, reconstruct a duration, release
        # its claim or touch snapshots/history. Retry explicitly archives it.
        job.update(status='error', reason=str(exc), timeAccountingError=True)
        write(jobpath, job)
        return
    budget.start()
    with budget.tools():
        _search_job(manager, folder, data, name, engine, claims, job, budget)


def _search_job(manager, folder, data, name, engine, claims, job, budget):
    settings = options(data['options'])
    owner = data['owner']; directory = folder / 'jobs' / name; jobpath = directory / 'job.json'
    job.setdefault('started', now())
    job.update(status='running', segmentStarted=now(), workerPid=os.getpid())
    def save():
        budget.checkpoint()
        write(jobpath, job)
    save()
    def stop_reason():
        if claims.holder(name) != owner:
            return 'Reservierung verloren; keine weiteren Builds'
        if budget.remaining() <= 0:
            return 'Zeitlimit erreicht'
        if job['attempts'] >= settings['maxAttempts']:
            return 'Versuchslimit erreicht'
        if (folder / 'stop.request').exists():
            return 'Vom Benutzer gestoppt'
        if not inputs_ok(data['pins']):
            return 'Compiler-/Werkzeug-/Referenzstand geändert; Suche angehalten'
        if not eligible(engine, name, owner, seed=True):
            return 'Funktion inzwischen zugeordnet oder anderweitig belegt'
        return None
    try:
        reason = stop_reason()
        if reason:
            job.update(status='queued' if reason == 'Vom Benutzer gestoppt' else ('review_ready' if job.get('best') else 'blocked'), reason=reason)
            save(); return
        place = engine.place(name)
        raw = engine.check.disc_bytes(place[:4])
        screen = candidate_preflight.inspect(raw, place[2]); job['preflight'] = screen
        if not screen['eligible']:
            job.update(status='blocked', reason='Vorprüfung: ' + '; '.join(screen['reasons']))
            return
        assembly = engine.asm(name) if job.get('inputMode', 'automatic') == 'automatic' else ''
        seed = directory / 'seed.c'
        if seed.exists() and not job.get('refreshDraft'):
            source = seed.read_text(encoding='utf-8')
        else:
            cached = candidates.record(engine, name)
            # A GP-dependent seed requires the same validated startup as m2c.
            # Other recognizers need no extra reference read.
            gp = rough.startup_gp(engine, name) if len(raw)==56 and int.from_bytes(raw[16:20],'little')&0xffff0000==0x8f820000 else None
            derived = structural_seeds.derive(name, raw, place[2], gp=gp)
            if derived:
                generated = dict(ok=True, source=derived[0]['source'], rule=derived[0]['rule'],
                                 facts=derived[0]['facts'], adapterVersion=rough.ADAPTER_VERSION,
                                 contextDeclarations=[], abiProblems=[])
            else:
                generated = cached['rough'] if cached and cached.get('buildable') else rough.decompile(engine, name, owner=owner, timeout=budget.timeout(45))
            write(directory / 'rough.json', generated)
            source = generated.get('source', '')
            raw_path = Path(generated['directory']) / 'm2c-output.c' if generated.get('directory') else None
            partial = source or (raw_path.read_text(encoding='utf-8') if raw_path and raw_path.exists() else '')
            if partial:
                rough_path = directory / 'rough.c'; rough_path.write_text(partial, encoding='utf-8')
                job['roughCandidate'] = {'snapshot': str(rough_path), 'sourceHash': sha(rough_path)}
            if not generated.get('ok'):
                job.update(status='blocked', reason='m2c konnte keinen vollständigen Grobentwurf erzeugen; Protokoll prüfen.')
                save(); return
            seed.write_text(source, encoding='utf-8')
        provenance = read(directory / 'rough.json') if (directory / 'rough.json').is_file() else {}
        if provenance.get('forwardedArguments'):
            job['forwardedArguments'] = provenance['forwardedArguments']
        # Explicit editor seeds are not assumed to be decompiler-generated.
        generated_seed = provenance.get('ok') and provenance.get('source') == source
        if generated_seed and provenance.get('abiProblems'):
            job.update(status='blocked', reason='ABI-Vorprüfung: ' + '; '.join(provenance['abiProblems']))
            return
        try:
            source, notes, unresolved = normalize(source, generated=bool(generated_seed),
                                                   assembly=assembly if generated_seed else '',
                                                   contracts=provenance.get('contextDeclarations', []))
        except ValueError as exc:
            job.update(status='blocked', reason='Typ-/Makro-Vorprüfung: '+str(exc))
            return
        job['normalizations'] = notes
        if unresolved:
            job.update(status='blocked', reason='Offene Operationen/Typen/Register: ' + ', '.join(unresolved) + '. Keine Werte geraten.')
            save(); return
        if generated_seed:
            source, pointer_notes, pointer_problems = pointer_seeds.repair(source, assembly, engine.root, generated=True)
            job['normalizations'] += pointer_notes
            if pointer_problems:
                job.update(status='blocked', reason='Zeigerschritt-Vorprüfung: ' + '; '.join(pointer_problems))
                return
        (directory / 'normalized-seed.c').write_text(source, encoding='utf-8')
        if re.search(r'\b(?:__asm__|asm|__attribute__|volatile)\b', code_only(source)):
            job.update(status='blocked', reason='Asm/Attribute/volatile benötigen eine manuelle Ausgangsprüfung.')
            save(); return
        profile = (compiler_variants.inspect(engine.root, timeout=budget.timeout(20))
                   if engine.config.get('adapter') == 'RAC4_PAL' and compiler_variants.has_divide(raw) else None)
        if profile:
            if profile['available'] and any(str(p.resolve()) not in data['pins'] for p in compiler_variants.inputs(engine.root)):
                profile.update(available=False, reason='Zusatzprofil war beim Sitzungsstart nicht vollständig erfasst; ausdrücklich neuen Suchpass starten')
            job['originalCompiler'] = profile
        variants = Variants(engine.root)
        cached_attempts = reusable_attempts(folder, data, name)
        frontier = deque([('Ausgangsentwurf', source, 0)])
        # Explicit released-result retries retain negative variants across app
        # versions, while recompiling their best seed against the new pins.
        historical_sources = prior_failed_sources(folder, data, name) if data.get('purpose') in ('retry-incomplete', 'unclaimed-and-open') else set()
        # A known negative BODY is not proof that today's transformations of
        # its source were exhausted. Compatible immutable comparisons can be
        # traversed once without another compiler run; unknown/incompatible
        # negatives stay excluded. Current-pass source keys still stop cycles.
        compiled_history = {key for key, result in cached_attempts.items()
                            if type(result.get('compiledSize')) is int and result['compiledSize'] > 0}
        seen_sources = set(historical_sources) - compiled_history
        seen_sources.discard(source_key(source))
        seen_objects = set(); stalled = 0
        queued = {source_key(text) for _, text, _ in frontier}
        generated_count = 0
        offered_sources = set(); history_skipped_sources = set()
        refresh_considered = False
        reviewed_considered = False
        while frontier:
            reason = stop_reason()
            if reason:
                break
            if job['attempts'] >= settings['maxAttempts']:
                reason = 'Versuchslimit erreicht'; break
            rule, source, depth = frontier.popleft()
            key = source_key(source)
            queued.discard(key)
            if key in seen_sources:
                continue
            seen_sources.add(key)
            reused = key in cached_attempts and same_cached_source(cached_attempts[key], source)
            if reused:
                result = cached_attempts[key]
                job['reusedBuilds'] = job.get('reusedBuilds', 0) + 1
            else:
                job['attempts'] += 1
                save()  # A crash must not silently refund compiler attempts or time.
                result = engine.compare(name, source, with_lines=False, owner=owner, timeout=budget.timeout(90))
            score = similarity(result)
            strict = engine.strict(name, result) if result.get('byteMatch') else {'passed': False, 'problems': [], 'placements': []}
            result['automaticStrict'] = strict
            if not reused:
                write(Path(result['snapshot']).with_name('result.json'), result)
            obj = Path(result['snapshot']).with_suffix('.o')
            fingerprint = object_key(obj, engine) if 'compiledSize' in result and obj.exists() else None
            duplicate = fingerprint is not None and fingerprint in seen_objects
            if fingerprint:
                seen_objects.add(fingerprint)
            record = dict(attempt=job['attempts'], rule=rule, depth=depth, score=score, duplicateObject=duplicate,
                          snapshot=result['snapshot'], sourceHash=result['sourceHash'], objectHash=sha(obj) if obj.exists() else None,
                          technicalMatch=strict['passed'], verdict=result['verdict'])
            record['reusedBuild'] = reused
            if job.get('contextRefresh', {}).get('sourceKey') == key and 'compiledSize' in result and not interrupted_build(result):
                job['contextRefresh']['state'] = 'built'
            if job.get('reviewedSeed', {}).get('sourceTextHash') == hashlib.sha256(source.encode('utf-8')).hexdigest():
                job['reviewedSeed']['state'] = ('built' if 'compiledSize' in result and not interrupted_build(result)
                                                else 'build-failed')
            job['lastAttempt'] = record
            with (directory / 'attempts.jsonl').open('a', encoding='utf-8') as log:
                log.write(json.dumps(record, ensure_ascii=False) + '\n')
            better = 'compiledSize' in result and (not job.get('best') or strict['passed'] and not job['best']['technicalMatch'] or score > job['best']['score'])
            if better:
                job['best'] = record
                (directory / 'best.c').write_text(Path(result['snapshot']).read_text(encoding='utf-8'), encoding='utf-8')
                stalled = 0
            else:
                stalled += 1
            job.update(staleAttempts=stalled, distinctObjects=len(seen_objects), updated=now())
            save()
            if not job.get('best') and 'compiledSize' not in result:
                reason = 'Grobentwurf noch nicht kompilierbar; Compilerprotokoll und letzten Entwurf prüfen'; break
            if strict['passed']:
                reason = 'Technischer Treffer; unabhängige vollständige Review erforderlich'; break
            # A plateau alone is not a stop: user explicitly wants the available
            # budget spent on remaining source variants, even after 20 equal builds.
            # Similarity is informational. Even 80--99% must not end a search
            # while supported variants and the configured budgets remain.
            # Equal machine output does not imply equal future source rewrites.
            # Explore source-distinct descendants under the same hard build/time budget.
            if depth < 6 and not stop_reason():
                try:
                    children = []
                    def offer(label, text):
                        key = source_key(text)
                        offered_sources.add(key)
                        if key in seen_sources:
                            if key in historical_sources:
                                history_skipped_sources.add(key)
                        elif key not in queued:
                            children.append((label, text, depth + 1)); queued.add(key)
                    # Offer the narrow, proven variant before the broad generic
                    # queue; otherwise a normal30-build budget may never see it.
                    try:
                        for label, text in review18_children(source, variants, engine, name, place, raw):
                            offer(label, text)
                    except Exception as exc:
                        job['variantNote'] = 'review18: ' + str(exc)[:800]
                    try:
                        for label, text in review19_children(source, variants, engine, name, place, raw):
                            offer(label, text)
                    except Exception as exc:
                        job['variantNote'] = 'review19: ' + str(exc)[:800]
                    try:
                        for label, text in reviewed_local_children(source, variants, engine, name, place, raw):
                            offer(label, text)
                    except Exception as exc:
                        job['variantNote'] = 'private-local-rules: ' + str(exc)[:800]
                    for label, text in (() if engine.config.get('adapter') != 'RAC4_PAL' else scheduler_variants.generate(source, raw)):
                        offer(label, text)
                    if profile:
                        for label, text in compiler_variants.generate(source, engine.root, raw, status=profile):
                            offer(label, text)
                    for label, text in variants.generate(source):
                        offer(label, text)
                    for module in (address_variants, review_variants, index_base_variants, native_guard_variants, terminal_return_variants, reset_store_variants, counter_relay_variants, postcall_default_variants, native_load_before_clear, adjacent_input_store_variants, shared_call_join_variants, word_base_variants, word_store_address_variants, terminated_switch_variants, native_sd16_profile, scalar_copy_temp):
                        try:
                            extra_children = (native_guard_children(source, engine, name, place, raw)
                                              if module is native_guard_variants
                                              else native_clear_children(source, engine, name, place, raw)
                                              if module is native_load_before_clear
                                              else word_base_children(source, engine, name, place, raw)
                                              if module is word_base_variants
                                              else word_store_children(source, engine, name, place, raw)
                                              if module is word_store_address_variants
                                              else native_profile_children(source, engine, name, place, raw)
                                              if module is native_sd16_profile
                                              else scalar_copy_temp.children(source, variants)
                                              if module is scalar_copy_temp
                                              else list(module.generate(source, engine.root)))
                        except Exception as exc:
                            extra_children = []
                            job['variantNote'] = module.__name__ + ': ' + str(exc)[:800]
                        for label, text in extra_children:
                            offer(label, text)
                    generated_count += len(children)
                    job['generatedVariants'] = generated_count
                    job['supportedVariantSources'] = len(offered_sources)
                    job['historySkippedVariants'] = len(history_skipped_sources)
                    if better:
                        frontier.extendleft(reversed(children))
                    else:
                        frontier.extend(children)
                    while len(frontier) > 300:
                        _, dropped, _ = frontier.pop(); queued.discard(source_key(dropped))
                except Exception as exc:
                    job['variantNote'] = str(exc)[:1000]
            # A manual improvement is another SEARCH INPUT, never a semantic
            # approval or a trusted compiler cache. The ordinary compare/strict
            # path above builds it under the same claim and global budgets.
            if not reviewed_considered:
                reviewed_considered = True
                if (job.get('reviewedSeed', {}).get('state') == 'prepared'
                        and job['attempts'] < settings['maxAttempts'] and not stop_reason()):
                    try:
                        planned = reviewed_seeds.plan(engine, folder, data, job, name)
                        if planned['eligible']:
                            text = planned.pop('source'); key = source_key(text)
                            job['reviewedSeed'].update(planned, state='accepted', sourceKey=key,
                                                      sourceTextHash=hashlib.sha256(text.encode('utf-8')).hexdigest())
                            if text == source:
                                job['reviewedSeed']['state'] = 'already-built'
                            else:
                                seen_sources.discard(key)
                                frontier = deque(item for item in frontier if source_key(item[1]) != key)
                                frontier.appendleft(('Offener Review-Zwischenstand als Suchbasis', text, 0))
                                queued.add(key)
                                while len(frontier) > 300:
                                    _, dropped, _ = frontier.pop(); queued.discard(source_key(dropped))
                        else:
                            job['reviewedSeed'].update(planned, state='rejected')
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        job['reviewedSeed'].update(state='rejected', reason=str(exc)[:800])
                    save()
            # The retained best is always built first. A prepared reviewed root
            # is tried before a potentially slow fresh m2c pass. A fresh sketch
            # remains an additional root only with newly evidenced declarations;
            # all roots share this loop's build/time/history budgets.
            if not refresh_considered and job.get('reviewedSeed', {}).get('state') != 'accepted':
                refresh_considered = True
                if job['attempts'] < settings['maxAttempts'] and not stop_reason():
                    try:
                        refresh = retry_context.plan(engine, folder, data, job, name)
                        job['contextRefresh'] = dict(refresh, state='not-needed')
                        if refresh['eligible']:
                            remaining = budget.remaining()
                            if remaining > 1:
                                generated = rough.decompile(engine, name, owner=owner, timeout=min(45, remaining))
                                write(directory / 'refresh-rough.json', generated)
                                text, notes = retry_context.prepare(generated, engine.asm(name), engine.root, refresh['declarations'])
                                key = source_key(text)
                                fresh = directory / 'normalized-fresh-context.c'; fresh.write_text(text, encoding='utf-8')
                                job['contextRefresh'].update(state='accepted', sourceKey=key, sourceHash=sha(fresh),
                                                              snapshot=str(fresh), normalizations=notes)
                                if key == source_key(source):
                                    job['contextRefresh']['state'] = 'history-covered'
                                else:
                                    # A known root may have unbuilt descendants.
                                    # Compatible cached comparisons avoid another
                                    # compiler run. Promote it even if a normal
                                    # rule already queued (or pruned) this text.
                                    seen_sources.discard(key)
                                    frontier = deque(item for item in frontier if source_key(item[1]) != key)
                                    frontier.appendleft(('Frischer Grobentwurf mit belegtem Kontext', text, 0)); queued.add(key)
                    except Exception as exc:
                        job['contextRefresh'] = dict(job.get('contextRefresh', {}), state='failed', error=str(exc)[:1000])
                        # A failed additional sketch never blocks or discards a
                        # previously usable best, nor changes its source/ABI.
                    save()
            reason = ('C-Analyse für weitere Varianten fehlgeschlagen; Suchdiagnose prüfen' if job.get('variantNote')
                      else 'Suchraum ausgeschöpft: keine weiteren unterstützten C-Varianten')
            if not frontier and not job.get('variantNote'):
                if not generated_count and history_skipped_sources:
                    reason = 'Keine neuen C-Varianten: Unterstützte Varianten wurden in vorherigen Suchpässen bereits geprüft'
                elif job['attempts'] == 1:
                    reason = 'Nur Ausgangsentwurf geprüft: Für diesen C-Code greifen noch keine Suchregeln'
        if not inputs_ok(data['pins'], full=True):
            reason = 'Werkzeug-/Referenzstand geändert; Ergebnisse erneut prüfen'
            if job.get('best'):
                job['best']['technicalMatch'] = False
        stopped = stop_reason()
        if stopped and not (job.get('best') or {}).get('technicalMatch'):
            reason = stopped
        job.update(status='queued' if reason == 'Vom Benutzer gestoppt' else ('review_ready' if job.get('best') else 'blocked'),
                   reason=reason or 'Suche beendet')
        if job['status'] != 'queued':
            job['finished'] = now()
    except search_time.Exhausted:
        job.update(status='review_ready' if job.get('best') else 'blocked', reason='Zeitlimit erreicht', finished=now())
    except subprocess.TimeoutExpired as exc:
        if budget.remaining() <= 0:
            job.update(status='review_ready' if job.get('best') else 'blocked', reason='Zeitlimit erreicht', finished=now())
        else:
            job.update(status='error', reason=str(exc)[:2000], finished=now())
    except Exception as exc:
        job.update(status='error', reason=str(exc)[:2000], finished=now())
    finally:
        if job.get('status') == 'blocked':
            job['deferred'] = True
        if job.get('status') == 'review_ready' and job.get('best'):
            try:
                from review_checks import write_report
                job['checks'] = write_report(engine,name,job['best'])
            except Exception as exc:
                # A diagnostic failure must never discard a saved candidate.
                job['checks'] = dict(error=str(exc),technicalPassed=False,manualReviewRequired=True)
        if job.get('status') not in ('running', 'queued'):
            job.setdefault('finished', now())
        if (job.get('status') in ('review_ready','blocked','error')
                and (directory/'attempts.jsonl').is_file() and claims.holder(name)==owner):
            try:
                compacted=retention.finish(manager.root,name,directory,job)
                if compacted:
                    job['retention']=compacted
            except Exception as exc:
                job['retention']=dict(error=str(exc)[:800],cleanupIncomplete=True)
        budget.checkpoint(finished=True)
        write(jobpath, job)


def process_pool(*, max_workers):
    return ProcessPoolExecutor(max_workers=max_workers, mp_context=multiprocessing.get_context('spawn'))


def process_search(repository, session, name):
    # Spawned workers share neither an interpreter/GIL nor a mutable engine.
    # Only paths/identifiers cross the boundary; claims remain on shared disk.
    return search_job(Manager(repository), session, name)


def sync_public_candidates(manager, data):
    data['publicReservation'] = {'status': 'read-only', 'checked': now()}


def run(manager, session, *, executor_factory=None, job_runner=None):
    with file_lock(manager.folder(session) / 'runner.guard'):
        return run_locked(manager, session, executor_factory=executor_factory, job_runner=job_runner)


def run_locked(manager, session, *, executor_factory=None, job_runner=None):
    folder = manager.folder(session)
    try:
        data = read(folder / 'session.json')
        assert_application_runtime(data)
        if data.get('version') != manager.engine.version:
            raise ValueError('Diese Spielversion wird von diesem Prüfkern nicht unterstützt.')
        assert_public_free(manager.engine, launch_names(manager, data), data['owner'])
        if not inputs_ok(data['pins'], full=True):
            raise ValueError('Werkzeuge/Referenz seit der Reservierung verändert. Neue Sitzung erforderlich.')
        pending = deque()
        for name in data.pop('nextRunNames', data['names']):
            jobpath = folder / 'jobs' / name / 'job.json'; job = read(jobpath)
            if job['status'] == 'running':
                job.update(status='review_ready' if job.get('best') else 'blocked', reason='Vorheriger Lauf unterbrochen; keine automatische Wiederholung.')
                write(jobpath, job)
            elif job['status'] == 'queued':
                pending.append(name)
        data.update(status='running', execution='processes', runner={'pid': os.getpid(), 'birth': process_birth(os.getpid()), 'started': now()}, heartbeat=now())
        write(folder / 'session.json', data)
        factory = executor_factory or process_pool
        worker = job_runner or process_search
        last_export = last_public = 0.0
        with factory(max_workers=data['options']['workers']) as pool:
            active = set()
            while pending or active:
                while pending and len(active) < data['options']['workers'] and not (folder / 'stop.request').exists():
                    active.add(pool.submit(worker, str(manager.root), session, pending.popleft()))
                if not active:
                    break
                done, active = wait(active, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    future.result()
                if done and time.monotonic()-last_export >= 2.5:
                    manager.export(session)
                    last_export = time.monotonic()
                if done and time.monotonic()-last_public >= 30:
                    sync_public_candidates(manager, data)
                    last_public = time.monotonic()
                data['heartbeat'] = now(); write(folder / 'session.json', data)
        still_queued = any(read(folder / 'jobs' / name / 'job.json')['status'] == 'queued' for name in data['names'])
        sync_public_candidates(manager, data)
        data.update(status='paused' if (folder / 'stop.request').exists() or still_queued else 'completed', finished=now())
        write(folder / 'session.json', data); manager.export(session)
    except BaseException as exc:
        data = read(folder / 'session.json'); data.update(status='error', error=str(exc), finished=now())
        write(folder / 'session.json', data)
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=['plan', 'start', 'run', 'status', 'stop', 'handoff', 'resume', 'retry'])
    ap.add_argument('--repo', required=True)
    ap.add_argument('--session'); ap.add_argument('--function', action='append', dest='names')
    ap.add_argument('--limit', type=int, default=10); ap.add_argument('--scope', default='both')
    for key in DEFAULTS:
        ap.add_argument('--' + key, type=int, default=DEFAULTS[key])
    args = ap.parse_args(); manager = Manager(args.repo)
    settings = options({k: getattr(args, k) for k in DEFAULTS})
    if args.command == 'plan':
        result = plan(manager.engine, args.limit, args.scope, settings=settings)
    elif args.command == 'start':
        names = args.names or [r['name'] for r in plan(manager.engine, args.limit, args.scope, settings=settings)]
        session = manager.create(names, settings)['id']
        result = manager.start(session)
    elif args.command == 'run':
        run(manager, args.session); result = manager.status(args.session)
    elif args.command == 'status':
        result = manager.status(args.session) if args.session else manager.sessions()
    elif args.command == 'stop':
        result = manager.stop(args.session)
    elif args.command == 'retry':
        result = manager.retry(args.session)
    elif args.command == 'handoff':
        if not args.names:
            ap.error('--function erforderlich')
        result = None
        for name in args.names:
            result = manager.handoff(args.session, name)
    else:
        result = manager.start(args.session)
    # Detached Windows runners may inherit a strict legacy output code page.
    # ASCII JSON escapes preserve the complete Unicode values on decoding.
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
