from pathlib import Path
import rough
from RAC4_PAL import compiler_variants, native_sd16_profile, ee296_profile
from project_engine import sha
def pin_inputs(engine, names):
    paths = set((engine.root / 'tools').glob('*.py')) | {engine.root / 'tools/cc.sh'}
    paths |= set((engine.root / 'include').rglob('*.h'))
    paths |= set((engine.root / 'toolchain/sn-prodg-3.01').rglob('*.exe'))
    paths |= set(compiler_variants.inputs(engine.root))
    paths |= set(native_sd16_profile.inputs(engine.root))
    paths |= set(ee296_profile.inputs(engine.root))
    paths |= set((engine.root / 'config/pal').glob('*.tsv')) - {engine.root / 'config/pal/map.tsv'}
    programs = {p[0] for n in names for p in engine.all_places(n)}
    for program in programs:
        engine.assert_reference(program)
        paths.add(Path(engine.levels.program_path(program) if program == 'main' else engine.levels.elf_path(int(program[6:]))))
    m2c = rough.tool(engine.root)
    if m2c:
        paths |= set(m2c.parent.glob('m2c/**/*.py')) | {m2c, m2c.parent / 'm2c_macros.h'}
    paths |= set(Path(__file__).parent.glob('*.py'))
    return {str(p.resolve()): {'sha256': sha(p), 'size': p.stat().st_size, 'mtime': p.stat().st_mtime_ns} for p in sorted(paths)}
