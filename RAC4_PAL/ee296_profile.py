"""Optional original EE296 file profile; no compiler or game data included.

An automatic owner must already have pinned every profile/support input. Merely
installing a compiler during a queued/running session never enables a new trial.
"""
from __future__ import annotations

import ast
import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from elftools.common.exceptions import ELFError

MARKER = '@ee296'
FLAGS = (MARKER, '-O2', '-G0')
FILES = {
    'bin/ee-gcc': 'b8c284d16c9c0a0e8788e9522c46881e224ad01fa1c732aed43f821cc32f168b',
    'lib/gcc-lib/ee/2.96-ee-001003-1/cc1': '59ec92b3f9f3513e0633331af304733e3094de30884e662a8cb584a51c1c42b5',
    'lib/gcc-lib/ee/2.96-ee-001003-1/cpp0': 'f6f6b598f39649edfe171d6da8c7f91d07f2b87c2aeeffffbc5f566bfb1a39a9',
    'runtime/ld-2.3.6.so': 'd5a16aee4a04db7fa27a5c071a0bef62002ee679bf5ecce51e383003cf840fd8',
    'runtime/libc.so.6': '7b091aee7173e3a7cbddc6630fa6b0d0106944b5618b0b27c2c058ea9c3d92eb',
}
ASSEMBLER_FILES = {
    'toolchain/sn-prodg-3.01/usr/local/sce/ee/gcc/bin/ee-gcc2953.exe': '522d28f9d74ddfce89568437156c8ee323c806de525d99907f698050dee8d81e',
    'toolchain/sn-prodg-3.01/usr/local/sce/ee/gcc/ee/bin/as.exe': '7f504e571215fead2a15a14520bd22dc13507a1f13b479ca8c06a165f4a886a4',
    'toolchain/sn-prodg-3.01/usr/local/sce/ee/gcc/lib/gcc-lib/ee/2.95.3/specs': '7b5520c0d9d04623b899ba80dbd713635a0aa17f0e58f7ba58b30c443cd48d76',
}
SUPPORT = (
    'tools/cc.sh', 'tools/ee296.py', 'tools/ee32_040921.py', 'tools/ee29_01.py',
    'include/common.h',
)
ABI_PROBE = '''#include "common.h"
typedef char ee296_int32[sizeof(int)==4 ? 1 : -1];
typedef char ee296_pointer32[sizeof(void *)==4 ? 1 : -1];
typedef char ee296_long64[sizeof(long)==8 ? 1 : -1];
typedef char ee296_scalar_sizes[sizeof(s8)==1 && sizeof(u8)==1 && sizeof(s16)==2 && sizeof(u16)==2 && sizeof(s32)==4 && sizeof(u32)==4 && sizeof(s64)==8 && sizeof(u64)==8 && sizeof(f32)==4 && sizeof(f64)==8 ? 1 : -1];
'''
_PROBES = {}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def root(repository):
    return Path(os.environ.get('EE296_ROOT', Path(repository) / 'build/isolated/ee-gcc2.96')).resolve()


def environment(repository):
    """Process-local transport settings, matching the existing game helper."""
    env = os.environ.copy()
    if any(env.get(key) for key in ('GCC_EXEC_PREFIX', 'COMPILER_PATH', 'GCC_SPECS', 'CPATH',
                                    'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'OBJC_INCLUDE_PATH')):
        raise ValueError('EE296 cannot verify external compiler/specs/include environment overrides.')
    env.update(EE296_ROOT=str(root(repository)), LANG='C', LC_ALL='C')
    settings = root(repository) / 'workbench.json'
    if settings.is_file():
        value = json.loads(settings.read_text(encoding='utf-8'))
        distro = value.get('wslDistro')
        if not isinstance(distro, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', distro):
            raise ValueError('Invalid EE296 WSL distribution in workbench.json.')
        env['EE296_WSL_DISTRO'] = distro
    env['WSLENV'] = ':'.join([p for p in env.get('WSLENV', '').split(':')
                            if p and p.split('/', 1)[0] not in ('LANG', 'LC_ALL')] + ['LANG', 'LC_ALL'])
    return env


def required_inputs(repository):
    repository = Path(repository).resolve()
    directory = root(repository)
    paths = {directory / relative for relative in FILES}
    paths.update(repository / relative for relative in ASSEMBLER_FILES)
    paths.update(repository / relative for relative in SUPPORT)
    paths.update((repository / 'include').rglob('*.h'))
    paths.update(Path(__file__).parent.glob('*.py'))
    paths.update(transport_inputs(Path(__file__).parents[1]))
    paths.add(repository / 'ps2decomp.json')
    if (directory / 'workbench.json').is_file():
        paths.add(directory / 'workbench.json')
    return sorted(paths)


def transport_inputs(shared):
    """Bind the transport's actual absolute search_time import, when present."""
    shared = Path(shared)
    importer = shared / 'local_process.py'
    try:
        tree = ast.parse(importer.read_text(encoding='utf-8'), filename=str(importer))
    except SyntaxError as exc:
        raise ValueError('EE296 cannot read the local_process.py import contract: ' + str(exc)) from exc
    imports_time = any(
        isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == 'search_time'
        or isinstance(node, ast.Import) and any(alias.name == 'search_time' for alias in node.names)
        for node in ast.walk(tree))
    # A missing required module must fail hashing, not silently remove a pin.
    return {importer, shared / 'search_time.py'} if imports_time else {importer}


def inputs(repository):
    """Called before the session starts; absent inputs are never synthesized."""
    return [p for p in required_inputs(repository) if p.is_file()]


def current_hashes(repository):
    paths = required_inputs(repository)
    hashes = {str(p.resolve()): _sha(p) for p in paths}
    for relative, expected in FILES.items():
        if hashes[str((root(repository) / relative).resolve())] != expected:
            raise ValueError('EE296 original compiler/runtime hash differs: ' + relative)
    for relative, expected in ASSEMBLER_FILES.items():
        if hashes[str((Path(repository) / relative).resolve())] != expected:
            raise ValueError('EE296 original assembler driver/backend/specs hash differs: ' + relative)
    return hashes


def fresh_jobs(repository):
    """Read queued first-run identities before search_job overwrites started.

    The shared worker does not persist cumulative elapsed time on resume. No
    elapsed value is reconstructed here: an existing execution marker means
    this optional profile is unavailable for that job's automatic owner.
    """
    repository = Path(repository).resolve()
    found, sessions = {}, {}
    for claim in (repository / 'build/claims').glob('func_*'):
        try:
            holder = claim.read_text(encoding='utf-8').split()[0]
            match = re.fullmatch(r'ps2decomp-auto-([0-9a-f]{32})', holder)
            if not match:
                continue
            session = match[1]
            folder = repository / 'build/autodecomp' / session
            if session not in sessions:
                sessions[session] = json.loads((folder / 'session.json').read_text(encoding='utf-8'))
            data = sessions[session]
            if (data.get('id') != session or data.get('owner') != holder or data.get('version') != 'pal'
                    or data.get('status') != 'running' or claim.name not in data.get('names', [])):
                continue
            job = json.loads((folder / 'jobs' / claim.name / 'job.json').read_text(encoding='utf-8'))
            if (job.get('name') != claim.name or job.get('status') != 'queued' or job.get('deferred', False)
                    or job.get('inputMode') != 'automatic' or type(job.get('attempts')) is not int or job['attempts'] != 0
                    or not isinstance(job.get('created'), str) or not job['created']
                    or any(key in job for key in ('started', 'elapsed', 'workerPid', 'previousPass',
                                                 'previousAttempts', 'lastAttempt', 'finished'))):
                continue
            # Capture no sources, bytes, claims or budget mutations.
            found[session, claim.name] = dict(created=job['created'], captured=time.monotonic(), pid=os.getpid())
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            continue
    return found


def pinned_session(engine, name, hashes, owner=None):
    """Read the active claim/session only; this confers no claim or permission."""
    if not isinstance(name, str) or not re.fullmatch(r'func_(?:L\d\d_)?[0-9A-F]{8}', name):
        raise ValueError('Unknown EE296 function identity.')
    claim = (Path(engine.root) / 'build/claims' / name).read_text(encoding='utf-8').split()
    holder = claim[0] if claim else ''
    found = re.fullmatch(r'ps2decomp-auto-([0-9a-f]{32})', holder)
    if not found or owner is not None and holder != owner:
        raise ValueError('EE296 automatic candidate requires its current automatic claim.')
    folder = Path(engine.root) / 'build/autodecomp' / found[1]
    data = json.loads((folder / 'session.json').read_text(encoding='utf-8'))
    job = json.loads((folder / 'jobs' / name / 'job.json').read_text(encoding='utf-8'))
    if (data.get('id') != found[1] or data.get('owner') != holder or data.get('version') != 'pal'
            or data.get('status') != 'running' or name not in data.get('names', [])
            or job.get('name') != name or job.get('status') != 'running' or job.get('deferred', False)):
        raise ValueError('EE296 needs the active non-deferred PAL search job.')
    witness = getattr(engine, '_ee296_fresh_jobs', {}).get((found[1], name), {})
    if (not isinstance(job.get('created'), str) or not job['created'] or job.get('inputMode') != 'automatic'
            or witness.get('created') != job['created'] or witness.get('pid') != os.getpid()
            or not isinstance(witness.get('captured'), (int, float)) or not math.isfinite(witness['captured'])
            or job.get('workerPid') != os.getpid()
            or any(key in job for key in ('previousPass', 'previousAttempts'))):
        raise ValueError('EE296 needs a witnessed first execution; resumed/unknown automatic time is unavailable.')
    pins = data.get('pins', {})
    if any(pins.get(path, {}).get('sha256') != digest for path, digest in hashes.items()):
        raise ValueError('EE296 profile/support inputs were absent or changed since session start.')
    profile_root = root(engine.root)
    include_root = (Path(engine.root) / 'include').resolve()
    adapter_root = Path(__file__).parent.resolve()
    for path in pins:
        original = Path(path)
        if (original.is_relative_to(profile_root) or original.is_relative_to(include_root)
                or original.is_relative_to(adapter_root) and original.suffix == '.py') and path not in hashes:
            raise ValueError('EE296 pinned profile/header/adapter input disappeared.')
    seconds = data.get('options', {}).get('seconds')
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('EE296 job time budget is unavailable.')
    started = datetime.datetime.fromisoformat(job['started'])
    elapsed = (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds()
    monotonic_elapsed = time.monotonic() - witness['captured']
    if elapsed < 0 or monotonic_elapsed < 0 or max(elapsed, monotonic_elapsed) >= seconds - 1:
        raise ValueError('EE296 job has no time remaining for an availability check.')
    return seconds - max(elapsed, monotonic_elapsed)


def inspect(repository, *, timeout=15):
    """Fresh hashes and a function-free ABI object, cached only by these inputs."""
    repository = Path(repository).resolve()
    status = dict(marker=MARKER, available=False, abiVerified=False, filesVerified=False)
    try:
        hashes = current_hashes(repository)
        env = environment(repository)
        settings = tuple((k, env.get(k)) for k in ('EE296_ROOT', 'EE296_WSL_DISTRO', 'LANG', 'LC_ALL', 'WSLENV',
                                                  'WINE', 'RAC4_CC_TMPDIR', 'RAC4_CC_CLEAN_TEMP'))
        key = (tuple(sorted(hashes.items())), settings, hashlib.sha256(ABI_PROBE.encode()).hexdigest())
        status.update(inputHashes=hashes, filesVerified=True)
        previous = _PROBES.get(key)
        if previous and _sha(previous['probeSource']) == previous['probeSourceHash'] and _sha(previous['probeObject']) == previous['probeObjectHash']:
            return dict(previous)
        folder = repository / 'build/isolated/probes' / ('ee296-' + uuid.uuid4().hex)
        folder.mkdir(parents=True)
        source, obj = folder / 'abi.c', folder / 'abi.o'
        source.write_text(ABI_PROBE, encoding='utf-8', newline='\n')
        bash = Path(r'C:\Program Files\Git\bin\bash.exe')
        env['PATH'] = str(bash.parent) + os.pathsep + env.get('PATH', '')
        command = [str(bash), 'tools/cc.sh', source.relative_to(repository).as_posix(), obj.relative_to(repository).as_posix(), *FLAGS]
        import local_process
        run = local_process.run(command, cwd=repository, env=env, timeout=timeout, text=True, errors='replace')
        (folder / 'compiler.log').write_text(run.stdout + run.stderr, encoding='utf-8')
        if run.returncode or not obj.is_file():
            raise ValueError('EE296 function-free ABI compilation failed.')
        from elftools.elf.elffile import ELFFile
        from elftools.elf.relocation import RelocationSection
        elf = ELFFile(io.BytesIO(obj.read_bytes()))
        if elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_MIPS' or elf['e_type'] != 'ET_REL':
            raise ValueError('EE296 ABI object format differs.')
        table = elf.get_section_by_name('.symtab')
        if (table is None or any(s['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT') for s in table.iter_symbols())
                or any(s['sh_flags'] & 6 and s['sh_size'] for s in elf.iter_sections())
                or any(isinstance(s, RelocationSection) and s.num_relocations() for s in elf.iter_sections())):
            raise ValueError('EE296 ABI probe unexpectedly defines code/data or relocations.')
        if hashes != current_hashes(repository):
            raise ValueError('EE296 inputs changed during the ABI probe.')
        status.update(available=True, abiVerified=True, reason='Verified original EE296 files and function-free scalar/pointer ABI.',
                      probeSource=str(source), probeObject=str(obj), probeSourceHash=_sha(source), probeObjectHash=_sha(obj),
                      command=command)
        (folder / 'proof.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
        if len(_PROBES) >= 8:
            _PROBES.pop(next(iter(_PROBES)))
        _PROBES[key] = dict(status)
        return status
    except (OSError, ValueError, KeyError, TypeError, ELFError, subprocess.TimeoutExpired) as exc:
        return dict(status, reason='EE296 unavailable: ' + str(exc)[:400])


def comparison_environment(engine, name, flags, owner, timeout):
    if tuple(flags) != FLAGS:
        raise ValueError('EE296 requires exactly @ee296 -O2 -G0 for the complete file.')
    if not isinstance(name, str) or not re.fullmatch(r'func_(?:L\d\d_)?[0-9A-F]{8}', name):
        raise ValueError('Unknown EE296 function identity.')
    def own_holder():
        expected = owner if owner is not None else getattr(engine, 'manual_owner', None)
        claim = (Path(engine.root) / 'build/claims' / name).read_text(encoding='utf-8').split()
        holder = claim[0] if claim else None
        if not expected or holder != expected:
            raise ValueError('EE296 needs the caller\'s existing own claim; no reservation is acquired here.')
        return holder

    holder = own_holder()
    automatic = holder.startswith(('ps2decomp-auto-', 'rac3-auto-', 'rac4-auto-'))
    hashes = current_hashes(engine.root)
    remaining = pinned_session(engine, name, hashes, owner=holder) if automatic else 15
    proof = inspect(engine.root, timeout=min(15, timeout, remaining))
    if not proof.get('available') or not proof.get('abiVerified'):
        raise ValueError(proof['reason'])
    if own_holder() != holder or proof['inputHashes'] != current_hashes(engine.root):
        raise ValueError('EE296 own claim or inputs changed during profile validation.')
    if automatic:
        remaining = pinned_session(engine, name, proof['inputHashes'], owner=holder)
    else:
        remaining = timeout
    return environment(engine.root), remaining
