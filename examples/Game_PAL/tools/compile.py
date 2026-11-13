"""Example compiler wrapper. Replace the compiler path/flags with game evidence."""
from pathlib import Path
import subprocess
import sys

source, output, function, program = sys.argv[1:5]
root = Path(__file__).resolve().parents[1]
compiler = root / 'toolchain/bin/ee-gcc.exe'
if not compiler.is_file():
    raise SystemExit('Configure the original compiler and proven flags in tools/compile.py first.')
# Illustrative invocation only: many PS2 toolchains require a separate assembler
# and object normalization. Preserve relocations, function symbols and sizes.
command = [str(compiler), '-c', source, '-o', output, '-I', str(root / 'include')]
raise SystemExit(subprocess.call(command, cwd=root))
