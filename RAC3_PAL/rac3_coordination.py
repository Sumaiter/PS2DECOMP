"""Read-only RAC3 issue-3 scopes and fresh upstream coverage; no public writes."""
import datetime
import fnmatch
import json
import re
import subprocess

URL = 'https://github.com/OpenRAC/rac3-decomp/issues/3'


def workflow(engine):
    """Keep the public-scope policy unless local matching is explicitly chosen."""
    value = getattr(engine, 'config', {}).get('coordination', {}).get('workflow', 'public-first')
    if value not in ('public-first', 'local-first'):
        raise ValueError('RAC3 coordination.workflow must be public-first or local-first.')
    return value


def run(args, cwd):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError('RAC3-Koordination konnte nicht frisch geprüft werden: ' + result.stderr[-1200:])
    return result.stdout


def scopes(body):
    first = body.split('\n\n', 1)[0]
    if re.search(r'no active claims|every scope.*released|all.*scopes.*released', first, re.I):
        return set(), set()
    if re.search(r'scope:\s*\n\s*-\s*none\b', body, re.I):
        return set(), set()
    names = set(re.findall(r'\bfunc_(?:L\d\d_)?[0-9A-F]{8}\b', body))
    if re.search(r'single functions only', body, re.I):
        return names, set()
    paths = set(re.findall(r'\bsrc/[A-Za-z0-9_./*?-]+', body))
    for line in body.splitlines():
        if 'src/levels/shared/' in line:
            paths.update('src/levels/shared/' + s for s in re.findall(r'\bL\d\d_[A-Za-z0-9_*?]+\.c', line))
        if 'src/levels/' in line and 'shared/' not in line:
            # Only abbreviated directory entries after the full first path.
            # A source file under a level must not reserve its entire directory.
            paths.update('src/levels/' + s for s in re.findall(r'(?<![\w/])\d\d_[a-z0-9_]+/(?=[\s,`)]|$)', line))
    return names, paths


def covers(name, path, entry):
    names, paths = entry
    return name in names or bool(path and any(path.startswith(p) if p.endswith('/') else fnmatch.fnmatchcase(path, p) for p in paths))


def snapshot(engine):
    raw = run(['gh', 'api', '--paginate', '--slurp', 'repos/OpenRAC/rac3-decomp/issues/3/comments'], engine.root)
    pages = json.loads(raw)
    if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
        raise ValueError('Unvollständige RAC3-Reservierungsliste.')
    comments = [c for page in pages for c in page]
    account = json.loads(run(['gh', 'api', 'user'], engine.root))['login']
    mine, other = [], []
    for comment in comments:
        entry = scopes(comment['body'])
        (mine if comment['user']['login'].lower() == account.lower() else other).append(entry)
    git = ['git', '-c', 'safe.directory=' + engine.root.as_posix(), '-C', str(engine.root)]
    run(git + ['fetch', '--quiet', 'origin', 'master'], engine.root)
    revision = run(git + ['rev-parse', 'FETCH_HEAD'], engine.root).strip()
    result = subprocess.run(git + ['grep', '-n', '-E', r'INCLUDE_ASM\(', revision, '--', 'src'],
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode not in (0, 1):
        raise ValueError('Frische RAC3-Quellabdeckung konnte nicht gelesen werden.')
    open_names = set()
    for line in result.stdout.splitlines():
        fields = line.split(':', 3)
        if len(fields) == 4:
            for name in re.findall(r'INCLUDE_ASM\([^;]*?,\s*(func_(?:L\d\d_)?[0-9A-F]{8})\s*\)', fields[3]):
                if engine.owners.get(name) == fields[1]:
                    open_names.add(name)
    report = dict(account=account, mine=mine, other=other, open=open_names, revision=revision)
    folder = engine.root / 'build/coordination'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'latest-read.json').write_text(json.dumps(dict(comments=comments, account=account, revision=revision,
        checked=datetime.datetime.now(datetime.timezone.utc).isoformat()), ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    return report


def foreign_source_units(engine, entries):
    """Reserve whole translation units, including catalogue-linked boot twins."""
    graph = {}
    for paths in engine.owner_paths.values():
        for path in paths:
            graph.setdefault(path, set()).update(paths - {path})
    held = {path for path in graph if any(covers('', path, entry) for entry in entries)}
    held.update(path for entry in entries for name in entry[0]
                for path in engine.owner_paths.get(name, {engine.owners.get(name)}) if path)
    todo = list(held)
    while todo:
        for path in graph.get(todo.pop(), ()):
            if path not in held:
                held.add(path)
                todo.append(path)
    return held


def owned(engine, name, entries):
    paths = engine.owner_paths.get(name, {engine.owners.get(name)})
    return bool(paths) and all(any(covers(name, path, entry) for entry in entries) for path in paths)


def blocked(engine, names=None, owner=None, progress=None, state=None, planning=False):
    state = state or snapshot(engine)
    targets = list(engine.places if names is None else names)
    foreign_units = foreign_source_units(engine, state['other'])
    held = {n for n in targets if n not in state['open'] or n not in engine.owners or
            any(path in foreign_units or any(covers(n, path, scope) for scope in state['other'])
                for path in engine.owner_paths.get(n, {engine.owners.get(n)}))}
    if held:
        from auto_jobs import PlacementIndex
        index = PlacementIndex(engine, ({}, set(), {}, {n: 'public' for n in held}))
        held |= {n for n in targets if index.overlaps(engine.all_places(n), n)}
    if planning and workflow(engine) == 'public-first' and state['mine']:
        held.update(n for n in targets if not owned(engine, n, state['mine']))
    if progress:
        progress(dict(stage='publicReservations', blocked=len(held), unresolved=0,
                      upstreamRevision=state['revision'], upstreamMatchRows=len(engine._matched)))
    return held


def assert_free(engine, names, owner=None, *, publication=False):
    if not names:
        return
    selected_workflow = workflow(engine)
    state = snapshot(engine)
    # Include unselected functions when propagating physical overlaps.
    conflicts = set(names) & blocked(engine, state=state)
    if conflicts:
        raise ValueError('RAC3: öffentlich reserviert, bereits integriert oder ungeklärt: ' + ', '.join(sorted(conflicts)))
    if selected_workflow == 'local-first' and not publication:
        return
    outside = [n for n in names if not owned(engine, n, state['mine'])]
    if outside:
        folder = engine.root / 'build/coordination'
        folder.mkdir(parents=True, exist_ok=True)
        paths = sorted({p for n in outside for p in engine.owner_paths.get(n, {engine.owners[n]})})
        comment = ('owner: ' + state['account'] + '\nupdated: ' + datetime.datetime.now(datetime.timezone.utc).isoformat()
                   + '\nversion: PAL (SCES_524.56), NTSC-U carried\n\nscope:\n'
                   + ''.join('  - ' + p + '\n' for p in paths))
        proposal = folder / 'reservation-comment-proposal.txt'
        proposal.write_text(comment, encoding='utf-8', newline='\n')
        if publication:
            raise ValueError('RAC3 PR: Ausgewählte Funktionen benötigen einen eigenen öffentlichen Quellbereich. '
                             'Ticket erneut prüfen und nur tatsächlich übernommene Ergebnisse reservieren. '
                             'Es wurde nichts veröffentlicht. Konkreter Entwurf: ' + str(proposal))
        if state['mine']:
            raise ValueError('Dein Kommentar in RAC3-Issue #3 ist vorhanden. Die Auswahl enthält jedoch '
                             + str(len(outside)) + ' Funktionen außerhalb seines Bereichs. '
                             'Bitte Funktionen laden und aus der aktualisierten Liste auswählen. '
                             'Entwurf für eine mögliche Bereichserweiterung: ' + str(proposal))
        raise ValueError('Vor neuer RAC3-Funktionsarbeit verlangt AGENTS.md einen eigenen Bereichskommentar in Issue 3. '
                         'Es wurde nichts veröffentlicht. Konkreter Entwurf: ' + str(proposal))


def assert_publication_ready(engine, names):
    """Recheck only the selected PR functions and require their public scopes.

    This never publishes a comment. Missing scopes produce a local proposal;
    the contributor must authorize and publish it, then run this check again.
    A local search claim does not grant priority over a later public reservation.
    """
    assert_free(engine, names, publication=True)
