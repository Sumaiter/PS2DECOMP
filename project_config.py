"""Per-game configuration; paths are rooted in the selected workspace."""
import hashlib
import json
from pathlib import Path
import re

FUNCTION = re.compile(r'func_(?:L\d\d_)?[0-9A-F]{8}\Z')


def load(repository):
    root = Path(repository).resolve()
    data = json.loads((root / 'ps2decomp.json').read_text(encoding='utf-8-sig'))
    if data.get('schemaVersion') != 1:
        raise ValueError('ps2decomp.json: schemaVersion must be 1.')
    for key in ('name', 'version', 'coordination'):
        if not data.get(key):
            raise ValueError('ps2decomp.json: missing ' + key)
    adapter = data.get('adapter', 'generic')
    if adapter != 'generic':
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*_(?:PAL|NTSC_U|NTSC_J)', adapter):
            raise ValueError('Adapter folders must follow GameName_PAL / GameName_NTSC_U / GameName_NTSC_J.')
        return data
    for key in ('programs', 'functions', 'compileCommand', 'inputs'):
        if not data.get(key):
            raise ValueError('ps2decomp.json: missing ' + key)
    if not isinstance(data['compileCommand'], list) or not all(isinstance(s, str) for s in data['compileCommand']):
        raise ValueError('compileCommand must be an argument array, never a shell command.')
    for key in ('source', 'object'):
        if not any('{' + key + '}' in s for s in data['compileCommand']):
            raise ValueError('compileCommand must include {' + key + '}.')
    for name, p in data['programs'].items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise ValueError('Invalid program identity.')
        path(root, p['elf'])
        if not re.fullmatch('[0-9a-fA-F]{64}', p.get('sha256', '')):
            raise ValueError(name + ': supply the original ELF SHA-256.')
    data.setdefault('mainProgram', next(iter(data['programs'])))
    if data['mainProgram'] not in data['programs']:
        raise ValueError('mainProgram must identify a configured program.')
    return data


def path(root, value):
    if not isinstance(value, str) or not value:
        raise ValueError('Expected a repository-relative file path.')
    result = (root / value).resolve()
    if Path(value).is_absolute() or not result.is_relative_to(root.resolve()):
        raise ValueError('Project input must stay inside the game repository: ' + value)
    return result


def integer(value):
    if isinstance(value, bool):
        raise ValueError('Expected an integer, not a boolean.')
    return int(value, 0) if isinstance(value, str) else int(value)


def verified_inputs(root, config):
    """Pin compiler binaries, scripts, headers and settings against supplied hashes."""
    files = set()
    for relative, expected in config['inputs'].items():
        p = path(root, relative)
        if not re.fullmatch('[0-9a-fA-F]{64}', expected) or hashlib.sha256(p.read_bytes()).hexdigest() != expected.lower():
            raise ValueError('Configured input SHA-256 differs: ' + relative)
        files.add(p)
    return files
