"""Offer a fresh automatic sketch only when evidenced declaration context grew.

Never rewrite the retained best, an editor seed, or any archived search input.
This is a second search root, not an ABI repair or a semantic approval.
"""
from pathlib import Path
import hashlib
import json
import re

import rough
import pointer_seeds
import seed_normalize
from seed_normalize import normalize
from search_rules import code_only, source_key


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def origin(folder, data, job, name):
    """Follow only session-local copied history; unknown/editor lineage is closed."""
    if data.get('purpose') != 'retry-incomplete' or job.get('inputMode') != 'retry':
        return None
    archives = {}
    for value in data.get('history', []):
        archive = Path(value)
        if archive.parent.resolve() != (folder / 'history').resolve():
            continue
        session = read(archive / 'session.json')
        packet = archive / 'jobs' / name
        if not (packet / 'job.json').is_file():
            continue
        sid = session.get('id')
        archives.setdefault(sid, []).append(packet)
    previous = job.get('previousSession'); visited = set()
    while previous and previous not in visited and len(visited) < 100:
        visited.add(previous)
        packets = archives.get(previous)
        if not packets:
            return None
        packet = packets[0]
        # Only ancestors on the selected lineage are relevant. Disagreement on
        # one of those ancestors remains ambiguous and closes the refresh path.
        for other in packets[1:]:
            for file in ('job.json', 'rough.json', 'seed.c'):
                if (other / file).exists() != (packet / file).exists():
                    return None
                if (other / file).exists() and (other / file).read_bytes() != (packet / file).read_bytes():
                    return None
        ancestor = read(packet / 'job.json')
        if ancestor.get('name') != name:
            return None
        mode = ancestor.get('inputMode')
        if mode == 'automatic':
            if not (packet / 'rough.json').is_file() or not (packet / 'seed.c').is_file():
                return None
            generated = read(packet / 'rough.json')
            if not generated.get('ok') or generated.get('source') != (packet / 'seed.c').read_text(encoding='utf-8'):
                return None
            if not isinstance(generated.get('contextDeclarations'), list):
                return None
            return generated
        if mode != 'retry':
            return None
        previous = ancestor.get('previousSession')
    return None


def plan(engine, folder, data, job, name):
    old = origin(folder, data, job, name)
    if old is None:
        return dict(eligible=False, reason='Kein belegter automatischer Ursprung; vorhandenen Entwurf beibehalten.')
    assembly = engine.asm(name)
    names = [name, *re.findall(r'\bfunc_(?:L\d\d_)?[0-9A-F]{8}\b', assembly)]
    declarations, conflicts = rough.context(engine.contracts, names)
    old_keys = {rough.context_key(d) for d in old['contextDeclarations']}
    added = [d for d in declarations if rough.context_key(d) not in old_keys]
    if not added or conflicts:
        return dict(eligible=False, reason='Kein neuer eindeutiger Signaturkontext.', conflicts=conflicts)
    script = rough.tool(engine.root)
    if script is None:
        return dict(eligible=False, reason='Lokales m2c fehlt; vorhandenen Entwurf beibehalten.')
    places = []
    for place in engine.all_places(name):
        engine.assert_reference(place[0])
        places.append([*place[:4], hashlib.sha256(engine.check.disc_bytes(place[:4])).hexdigest()])
    if not places:
        return dict(eligible=False, reason='Keine belegten Originalplatzierungen.')
    identity = dict(declarations=sorted((rough.context_key(d) for d in declarations), key=repr), places=places, toolHash=hashlib.sha256(script.read_bytes()).hexdigest(),
                    adapter=rough.ADAPTER_VERSION,
                    helperHashes={Path(m.__file__).name: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                                  for m in (rough, pointer_seeds, seed_normalize)},
                    toolDependencies={str(Path(p).relative_to(script.parent)): pin['sha256']
                                      for p,pin in data.get('pins',{}).items() if Path(p).is_relative_to(script.parent)})
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    # Building a root does NOT prove that all descendants were explored. Offer
    # it again; the search loop reuses compatible immutable compiler evidence
    # while allowing new, previously unbuilt descendants under the same budget.
    return dict(eligible=True, reason='Zusätzlicher belegter Signaturkontext verfügbar.', fingerprint=fingerprint,
                declarations=declarations, addedDeclarations=added, identity=identity)


def prepare(generated, assembly, root, declarations):
    """The normal automatic-source guards must also accept the second root."""
    if not generated.get('ok') or generated.get('abiProblems'):
        raise ValueError('Frischer Grobentwurf unvollständig oder ABI-Prüfung offen.')
    if generated.get('contextDeclarations') != declarations:
        raise ValueError('Signaturkontext während des Grobentwurfs verändert.')
    source, notes, unresolved = normalize(generated.get('source', ''), generated=True,
                                          assembly=assembly, contracts=declarations)
    if not source.strip() or unresolved:
        raise ValueError('Frischer Grobentwurf enthält offene Operationen oder Werte.')
    source, pointer_notes, problems = pointer_seeds.repair(source, assembly, root, generated=True)
    if problems:
        raise ValueError('Zeigerschritt-Vorprüfung offen: ' + '; '.join(problems))
    if re.search(r'\b(?:__asm__|asm|__attribute__|volatile)\b', code_only(source)):
        raise ValueError('Frischer Grobentwurf benötigt eine manuelle Ausgangsprüfung.')
    return source, [*notes, *pointer_notes]
