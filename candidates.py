"""Evidence-based picker of compilable drafts, not completed decompilations."""
import hashlib
import json
import re
from pathlib import Path
import rough
import function_size


def digest(data):
    return hashlib.sha256(data).hexdigest()


def environment(engine):
    return engine.environment()


def local_finals(engine):
    result = set()
    for path in (engine.root / 'build').glob('parallel*/[abcr]*/matches.json'):
        result.update(json.loads(path.read_text()).get('functions', {}))
    return result


def available(engine, name, inventory=None, finals=None):
    place = engine.place(name)
    names, matched, published, claims = inventory or engine.inventory_state()
    held_elsewhere = name in claims and claims[name] != getattr(engine, 'manual_owner', None)
    if name in names or name in published or held_elsewhere or place[:3] in matched:
        return False
    return name not in (local_finals(engine) if finals is None else finals)


def boundary(engine, name):
    place = engine.place(name)
    if not function_size.contains(place[3], getattr(engine, 'function_size_range', None)) or place[3] % 4 or place[4] == 'remnant':
        return False
    exclusions = engine.root / 'build/parallel01/excluded-boundaries.json'
    if exclusions.exists() and name in json.loads(exclusions.read_text()):
        return False
    assembly = engine.asm(name)
    instructions = re.findall(r'/\* ([0-9A-F]+) [0-9A-F]+ \*/\s+([^\n]+)', assembly)
    if len(instructions) * 4 != place[3]:
        return False
    if re.match(r'addiu\s+\$sp,\s*\$sp,\s*(?:0x)?[0-9]', instructions[0][1]):
        return False
    operations = [text.split()[0] for _, text in instructions]
    if any(op.startswith('v') or op in ('lqc2', 'sqc2', 'qmtc2', 'qmfc2', 'teq', 'break', 'syscall') for op in operations):
        return False
    for label in re.findall(r'\.L([0-9A-F]{8})', assembly):
        if not place[2] <= int(label, 16) < place[2] + place[3]:
            return False
    # Conservative screen, not proof of the original function extent.
    returns = [i for i, (_, text) in enumerate(instructions) if re.match(r'jr\s+\$ra\b', text)]
    if not returns or any(i + 1 >= len(instructions) for i in returns):
        return False
    last_return = max(returns)
    return all(text.strip() == 'nop' for _, text in instructions[last_return + 2:])


def record_path(engine, name):
    return engine.root / 'build/studio/buildable' / (name + '.json')


def record(engine, name, env_hash=None):
    path = record_path(engine, name)
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
        if result['environment'] != (env_hash or environment(engine)):
            return None
        if result['retailHash'] != digest(engine.check.disc_bytes(engine.place(name)[:4])):
            return None
        if result.get('buildable'):
            snapshot = Path(result['snapshot'])
            if not snapshot.is_file() or digest(snapshot.read_bytes()) != result['sourceHash']:
                return None
            if not result.get('rough') or digest(result['rough']['source'].encode('utf-8')) != result['draftHash']:
                return None
        return result
    except (ValueError, KeyError, OSError):
        return None


def search(engine, query=''):
    inventory = engine.inventory_state()
    finals = local_finals(engine)
    env_hash = environment(engine)
    result = []
    for path in (engine.root / 'build/studio/buildable').glob('func_*.json'):
        name = path.stem
        if name not in engine.places or query.upper().replace('0X', '') not in name.upper():
            continue
        if not function_size.contains(engine.place(name)[3], getattr(engine, 'function_size_range', None)):
            continue
        if not available(engine, name, inventory, finals):
            continue
        cached = record(engine, name, env_hash)
        if cached and cached['buildable']:
            place = engine.place(name)
            result.append({'name': name, 'size': place[3], 'section': place[1], 'state': 'Grobentwurf kompiliert · Match offen', 'claim': '', 'buildable': True})
    return sorted(result, key=lambda item: (item['size'], item['name']))[:120]


def plan(engine, limit=40):
    inventory = engine.inventory_state()
    finals = local_finals(engine)
    env_hash = environment(engine)
    pool = []
    for name, place in sorted(engine.places.items(), key=lambda pair: (pair[1][3], pair[0])):
        if not function_size.contains(place[3], getattr(engine, 'function_size_range', None)) or not available(engine, name, inventory, finals):
            continue
        if record(engine, name, env_hash) is not None or not boundary(engine, name):
            continue
        pool.append(name)
        if len(pool) >= min(max(int(limit), 1), 100):
            break
    return pool


def probe(engine, name):
    if not available(engine, name) or not boundary(engine, name):
        return {'name': name, 'buildable': False, 'reason': 'Bereits bearbeitet/reserviert oder keine geeignete Funktionsgrenze.'}
    result = {'name': name, 'environment': environment(engine), 'retailHash': digest(engine.check.disc_bytes(engine.place(name)[:4])), 'buildable': False}
    generated = rough.decompile(engine, name)
    if not generated['ok']:
        result['reason'] = 'm2c konnte keinen vollständigen Grobentwurf erzeugen.'
    elif generated.get('abiProblems'):
        result['reason'] = 'Offene ABI-/Instruktionsbelege: ' + '; '.join(generated['abiProblems'])
    elif re.search(r'\b(?:M2C_\w+|saved_reg_\w+)\b', re.sub(r'/\*.*?\*/|//[^\n]*', '', generated['source'], flags=re.S)):
        result['reason'] = 'Unaufgelöste Typen/Operationen oder Register im Grobentwurf.'
    else:
        compiled = engine.compare(name, generated['source'], with_lines=False)
        result.update(buildable='compiledSize' in compiled, snapshot=compiled['snapshot'], sourceHash=compiled['sourceHash'], draftHash=digest(generated['source'].encode('utf-8')),
                      reason='Kanonische Kompilierung bestanden; Bedeutung und Matching noch offen.' if 'compiledSize' in compiled else 'Grobentwurf kompiliert noch nicht.', rough=generated)
    path = record_path(engine, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    return {key: value for key, value in result.items() if key != 'rough'}


def prepare(engine, name):
    if not available(engine, name):
        raise ValueError('Die Funktion ist inzwischen bearbeitet oder reserviert. Bitte Auswahl aktualisieren.')
    cached = record(engine, name)
    if not cached or not cached['buildable']:
        raise ValueError('Kein aktueller kompilierter Grobentwurf. Bitte Auswahlliste erneut prüfen.')
    return dict(engine.create(name, cached['rough']['source']), rough=cached['rough'])
