"""Project-owned read-only coordination hook. No inherited GitHub permissions."""
import json
import subprocess


def state(engine, *, discovery=False):
    policy = engine.config['coordination']
    if engine.config.get('adapter', 'generic') != 'generic':
        if policy.get('mode') != 'native':
            raise ValueError('A game adapter requires its native coordination gate.')
        import importlib
        adapter = importlib.import_module(engine.config['adapter'])
        check = (getattr(adapter, 'candidate_coordination_state', adapter.coordination_state)
                 if discovery else adapter.coordination_state)
        return check(engine)
    if policy.get('mode') == 'local':
        return set(), None
    if policy.get('mode') != 'command':
        raise ValueError('Configure coordination.mode as local or command.')
    command = policy.get('command')
    if not isinstance(command, list) or not command:
        raise ValueError('coordination.command must be an argument array.')
    args = [s.replace('{python}', __import__('sys').executable).replace('{root}', str(engine.root)) for s in command]
    from local_process import run
    result = run(args, cwd=engine.root, text=True, encoding='utf-8', timeout=60)
    if result.returncode:
        raise ValueError('Fresh coordination check failed: ' + result.stderr[-1500:])
    data = json.loads(result.stdout)
    if data.get('complete') is not True or data.get('version') != engine.version:
        raise ValueError('Coordination is incomplete or belongs to another game version.')
    if not isinstance(data.get('blocked'), list) or not isinstance(data.get('allowed'), list):
        raise ValueError('Coordination requires explicit blocked and allowed function lists.')
    # The project hook checks upstream integration, version-specific reservations,
    # contributor capacity and own public scope. Missing permission fails closed.
    return set(data['blocked']), set(data['allowed'])


def blocked(engine, names=None, owner=None, progress=None, *, discovery=False):
    held, allowed = state(engine, discovery=discovery)
    targets = set(engine.places if names is None else names)
    if allowed is not None:
        held |= targets - allowed
    from auto_jobs import PlacementIndex
    index = PlacementIndex(engine, ({}, set(), {}, {n: 'public' for n in held if n in engine.places}))
    result = {n for n in targets if n in held or index.overlaps(engine.all_places(n), n)}
    if progress:
        progress(dict(stage='publicReservations', blocked=len(result), unresolved=0))
    return result


def assert_free(engine, names, owner=None):
    conflicts = blocked(engine, names, owner)
    if conflicts:
        raise ValueError('Reserved, integrated or outside the allowed contributor scope: ' + ', '.join(sorted(conflicts)))
