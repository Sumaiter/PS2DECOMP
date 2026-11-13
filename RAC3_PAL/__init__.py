"""Game-specific PAL integration. No game data or proprietary tools."""
from .rac3_engine import Engine


def version_availability(repository):
    return Engine.version_availability(repository)


def create_engine(repository, settings, version=None):
    return Engine(repository, version=version or settings['version'])


def coordination_state(engine):
    from . import rac3_coordination as service
    state = service.snapshot(engine)
    blocked = service.blocked(engine, state=state)
    # Selection and start must both respect the contributor's existing scope.
    allowed = {n for n in engine.places if any(service.covers(n, engine.owners.get(n), scope) for scope in state['mine'])}
    return blocked, allowed
