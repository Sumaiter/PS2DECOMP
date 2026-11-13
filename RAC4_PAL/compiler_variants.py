"""Optional original compiler trials; no changes to C, ABI or emitted assembly."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import uuid
import local_process

MARKER = '@ee29-01'
FILES = {
    'bin/ee-gcc': '64d0a50fef499da0b98177eb5e79e41dfb066ad44246b137c78a266ef97ee265',
    'lib/gcc-lib/ee/2.9-ee-991111-01/cc1': 'b9aef69f93efb949f15ea58189e8eef4a002b9fe4de5d3fcf89c34e1244ec026',
    'lib/gcc-lib/ee/2.9-ee-991111-01/cpp': 'f85a54d241e019993fa7a06285d3677856b3b431e10318a587ab029373c603d7',
    'runtime/ld-2.3.6.so': 'd5a16aee4a04db7fa27a5c071a0bef62002ee679bf5ecce51e383003cf840fd8',
    'runtime/libc.so.6': '7b091aee7173e3a7cbddc6630fa6b0d0106944b5618b0b27c2c058ea9c3d92eb',
}
ABI_PROBE = '''#include "common.h"
typedef char rac4_int32[sizeof(int)==4 ? 1 : -1];
typedef char rac4_pointer32[sizeof(void *)==4 ? 1 : -1];
typedef char rac4_long64[sizeof(long)==8 ? 1 : -1];
typedef char rac4_scalars[sizeof(s8)==1 && sizeof(s16)==2 && sizeof(s32)==4 && sizeof(s64)==8 && sizeof(u64)==8 && sizeof(f32)==4 && sizeof(f64)==8 ? 1 : -1];
'''


def root(repository):
    return Path(os.environ.get('EE29_01_ROOT', Path(repository) / 'build/isolated/ee-gcc2.9-991111-01')).resolve()


def environment(repository):
    env = os.environ.copy()
    # The pinned legacy glibc cannot read modern WSL locale archives. Its
    # built-in C locale needs no archive; keep this override child-local.
    env.update(LANG='C', LC_ALL='C')
    env['WSLENV'] = ':'.join(
        [entry for entry in env.get('WSLENV', '').split(':')
         if entry and entry.split('/', 1)[0] not in ('LANG', 'LC_ALL')] + ['LANG', 'LC_ALL'])
    settings = root(repository) / 'workbench.json'
    if settings.is_file():
        value = json.loads(settings.read_text(encoding='utf-8'))
        distro = value.get('wslDistro')
        if not isinstance(distro, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', distro):
            raise ValueError('Ungültige WSL-Distribution in workbench.json')
        env.setdefault('EE29_01_WSL_DISTRO', distro)
    return env


def inputs(repository):
    directory = root(repository)
    return [p for p in [*(directory / n for n in FILES), directory / 'workbench.json'] if p.is_file()]


def has_divide(raw):
    return bool(raw and len(raw) % 4 == 0 and any(
        w >> 26 == 17 and w >> 21 & 31 == 16 and w & 63 == 3
        for (w,) in struct.iter_unpack('<I', raw)))


def inspect(repository, *, probe=True, timeout=20):
    repository = Path(repository)
    value = dict(marker=MARKER, available=False, filesVerified=False, abiVerified=False,
                 root=str(root(repository)))
    try:
        for name, expected in FILES.items():
            path = root(repository) / name
            if not path.is_file():
                return dict(value, reason='Originalcompiler nicht installiert: ' + name)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return dict(value, reason='Originalcompiler-Prüfsumme abweichend: ' + name)
        value['filesVerified'] = True
        if not (repository / 'tools/ee29_01.py').is_file() or MARKER not in (repository / 'tools/cc.sh').read_text():
            return dict(value, reason='Repository unterstützt das Originalcompilerprofil noch nicht')
        env = environment(repository)
        distro = env.get('EE29_01_WSL_DISTRO', 'docker-desktop')
        value['wslDistro'] = distro
        if os.name == 'nt':
            check = local_process.run(['wsl.exe', '--list', '--quiet'], cwd=repository, timeout=5)
            output = check.stdout.decode('utf-16-le' if b'\0' in check.stdout else 'utf-8', errors='replace')
            if check.returncode:
                errors = check.stderr.decode('utf-16-le' if b'\0' in check.stderr else 'utf-8', errors='replace')
                diagnostic = ' '.join((output+' '+errors).replace('\ufeff','').split())[:300]
                if 'E_ACCESSDENIED' in diagnostic:
                    return dict(value, wslDiagnostic=diagnostic,
                                reason='WSL-Zugriff verweigert; Suchprozess im normalen Benutzerkontext neu starten')
                return dict(value, wslDiagnostic=diagnostic,
                            reason='WSL-Abfrage fehlgeschlagen: '+(diagnostic or 'Fehlercode '+str(check.returncode)))
            if distro not in [line.strip().lstrip('\ufeff') for line in output.splitlines()]:
                return dict(value, reason='Compilerdateien geprüft; WSL-Distribution ' + distro + ' ist nicht registriert')
        if not probe:
            return dict(value, reason='Compilerdateien geprüft; ABI-Ausführungsprobe noch ausstehend')
        folder = repository / 'build/isolated/probes' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        source, assembly = folder / 'abi.c', folder / 'abi.s'
        source.write_text(ABI_PROBE, encoding='utf-8', newline='\n')
        run = local_process.run([sys.executable, str(repository / 'tools/ee29_01.py'), str(source), str(assembly), '-O2', '-G0'],
                                cwd=repository, env=env, timeout=timeout, text=True, errors='replace')
        (folder / 'compiler.log').write_text(run.stdout + run.stderr, encoding='utf-8')
        value['probe'] = str(folder)
        if run.returncode or not assembly.is_file():
            return dict(value, reason='Originalcompiler nicht ausführbar; ABI-Probe fehlgeschlagen')
        return dict(value, available=True, abiVerified=True, reason='Originalcompiler geprüft und ausführbar; zusätzliche Variante bei float-Divisionen')
    except (OSError, ValueError, TimeoutError) as exc:
        return dict(value, reason='Originalcompiler nicht verfügbar: ' + str(exc)[:300])
    except subprocess.TimeoutExpired:
        return dict(value, reason='Originalcompiler-Laufzeitprüfung hat das Zeitlimit erreicht')


def generate(source, repository, raw, *, status):
    if not status.get('available') or not status.get('abiVerified') or not has_divide(raw):
        return
    # Explicit user flags remain unchanged. Only the default route and the
    # equivalent existing EE29 -O2/-G0 route receive a separate profile trial.
    comments = re.findall(r'/\*\s*cflags:\s*(.*?)\*/', source, re.S)
    if comments and (len(comments) != 1 or comments[0].split() not in (['@ee29'], ['@ee29', '-O2', '-G0'])):
        return
    # Preprocessor/path-dependent inputs and asm/intrinsics need manual review.
    from search_rules import code_only
    clean = code_only(source)
    directives = re.findall(r'^\s*#.*$', clean, re.M)
    if any(not re.fullmatch(r'\s*#\s*include\s*"common.h"\s*', d) for d in directives):
        return
    if re.search(r'\b(?:asm|__asm__|__builtin_\w+|__attribute__|__FILE__|__LINE__|__DATE__|__TIME__)\b', clean):
        return
    body = re.sub(r'/\*\s*cflags:.*?\*/', '', source, flags=re.S).lstrip('\r\n')
    yield 'Originalcompiler EE 2.9-991111-01', '/* cflags: @ee29-01 -O2 -G0 -Wa,-G0 */\n' + body
