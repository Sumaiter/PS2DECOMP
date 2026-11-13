"""Run RAC3's full Python build pipeline with native Windows tools.

The upstream build.py uses POSIX wrapper scripts; the older Makefile omits
core_rodata. This adapter retains the Python pipeline and invokes EXEs directly.
"""
from pathlib import Path
import argparse
import os
import re
import subprocess
import sys


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--target', default='all')
    ap.add_argument('-j', type=int, default=8)
    a = ap.parse_args()
    root = a.repo.resolve()
    os.chdir(root)
    sys.path.insert(0, str(root / 'tools'))
    import targets
    import core_rodata
    tc = root / 'build/toolchains/sn'
    ee29 = root / 'build/toolchains/ee29'
    os.environ['UYA_EE29'] = str(ee29)
    os.environ['TMPDIR'] = str(root / 'build/tmp')
    (root / 'build/tmp').mkdir(parents=True, exist_ok=True)
    asflags = ['-I', 'include', '-EL', '-mips3', '-mcpu=5900', '-mabi=eabi']

    def run(cmd):
        print('+ ' + subprocess.list2cmdline([str(x) for x in cmd]), flush=True)
        subprocess.run([str(x) for x in cmd], cwd=root, check=True)

    selected = list(targets.TARGETS.values()) if a.target == 'all' else [targets.get(a.target)]
    for t in selected:
        os.environ['UYA_TARGET'] = t.name
        print('=== ' + t.name + ' ===', flush=True)
        script = (root / t.ld).read_text()
        prefix = t.build_dir.replace('\\', '/').rstrip('/') + '/'
        for obj in dict.fromkeys(re.findall(r'(%s(?:asm/\S+?\.s)\.o)' % re.escape(prefix), script)):
            src = obj[len(prefix):-2]
            (root / obj).parent.mkdir(parents=True, exist_ok=True)
            run([tc / 'bin/ee-as.exe', *asflags, '-o', obj, src])
        cflags = ('-I include -I . -Wa,-I,include,-mips3,-mcpu=5900,-mabi=eabi '
                  '-DINCLUDE_ASM_USE_MACRO_INC=1 -B' + (tc / 'bin/ee-').as_posix())
        for u in t.units:
            run([sys.executable, 'tools/build_text.py', '--target', t.name,
                 '--unit', u.name, '--cc', tc / 'bin/ee-gcc2953.exe',
                 '--ld', tc / 'bin/ee-ld.exe', '--cflags', cflags,
                 '--ee29', ee29, '-j', str(a.j), '-o', t.obj(u)])
        derived = root / t.build_dir / (t.name + '.ld')
        if derived.exists():
            derived.unlink()  # adapter-owned derived linker script, never source
        if targets.is_level(t):
            run([sys.executable, 'tools/level_symbols.py', '--target', t.name])
        else:
            core_rodata.derive(t)
        ld = derived.relative_to(root).as_posix() if derived.exists() else t.ld
        if derived.exists():
            pieces = re.findall(r'(%spieces/\S+?\.s)\.o' % re.escape(prefix), derived.read_text())
            for src in dict.fromkeys(pieces):
                run([tc / 'bin/ee-as.exe', *asflags, '-o', src + '.o', src])
        elf = Path(t.build_dir) / (t.name + '.elf')
        binary = Path(t.build_dir) / t.bin_name
        run([tc / 'bin/ee-ld.exe', '-T', ld, '-o', elf])
        if getattr(t, 'flatten', 'objcopy') == 'elf2bin':
            run([sys.executable, 'tools/elf2bin.py', elf, binary])
        else:
            run([tc / 'bin/ee-objcopy.exe', '-O', 'binary', elf, binary])
        run([sys.executable, 'tools/check_match.py', '--target', t.name, binary, t.elf, t.yaml])


if __name__ == '__main__':
    main()
