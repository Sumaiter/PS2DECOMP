"""Run an unchanged project build tool with bounded native linker arguments.

The RAC3 project links more than a thousand objects in one ld -r command.
Windows cannot create that process once its argument string exceeds 32767
characters. An implicit GNU INPUT script preserves the object list and order
without replacing the default linker script, a compiler flag or an object.
This old linker does not accept modern argv-style @response files. Only the
configured linker's exact ld -r -o OUTPUT OBJECT... form is wrapped.
"""
from pathlib import Path
import os
import runpy
import subprocess
import sys
import tempfile


def quote_argument(value):
    if any(char in value for char in '\x00\r\n'):
        raise ValueError('A linker argument contains an unsupported control character.')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def response_runner(run, linker, directory, *, limit=30000):
    linker = Path(linker).resolve()
    directory = Path(directory)

    def wrapped(command, *args, **kwargs):
        if (not isinstance(command, (list, tuple)) or len(command) < 5
                or kwargs.get('shell')
                or Path(command[0]).resolve() != linker
                or tuple(command[1:3]) != ('-r', '-o')
                or any(not str(part).lower().endswith('.o') or str(part).startswith('-')
                       for part in command[4:])
                or len(subprocess.list2cmdline([str(part) for part in command])) <= limit):
            return run(command, *args, **kwargs)
        directory.mkdir(parents=True, exist_ok=True)
        encoding = 'mbcs' if os.name == 'nt' else 'utf-8'
        path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.ld', prefix='rac3-objects-',
                                             dir=directory, encoding=encoding,
                                             newline='\n', delete=False) as stream:
                path = Path(stream.name)
                stream.write('INPUT(\n' + '\n'.join(quote_argument(str(part)) for part in command[4:]) + '\n)\n')
            return run([*[str(part) for part in command[:4]], str(path)], *args, **kwargs)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
    return wrapped


def main():
    # This child runs the project's original Python script. Its subprocess
    # calls use the same arguments, apart from transport for a long ld call.
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--linker', type=Path, required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    if options.arguments[:1] == ['--']:
        options.arguments.pop(0)
    if not options.arguments:
        parser.error('Project build script required.')
    root = options.repo.resolve()
    script = (root / options.arguments[0]).resolve()
    if script != root / 'tools/build_text.py':
        parser.error('Only the project build_text.py entry point is supported.')
    os.chdir(root)
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script), *options.arguments[1:]]
    original_run = subprocess.run
    subprocess.run = response_runner(original_run, options.linker, root / 'build/tmp')
    try:
        runpy.run_path(str(script), run_name='__main__')
    finally:
        subprocess.run = original_run


if __name__ == '__main__':
    main()
