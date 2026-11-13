"""Game-specific PAL integration. No game data or proprietary tools."""
from .rac3_engine import Engine


def version_availability(repository):
    return Engine.version_availability(repository)


def create_engine(repository, settings, version=None):
    return Engine(repository, version=version or settings['version'])


def coordination_state(engine):
    from . import rac3_coordination as service
    workflow = service.workflow(engine)
    state = service.snapshot(engine)
    blocked = service.blocked(engine, state=state)
    # Local matching still checks foreign scopes and upstream integration.
    allowed = (None if workflow == 'local-first' else
               {n for n in engine.places if service.owned(engine, n, state['mine'])})
    return blocked, allowed


def candidate_coordination_state(engine):
    """Browse unclaimed scopes without granting permission to start work."""
    from . import rac3_coordination as service
    service.workflow(engine)
    state = service.snapshot(engine)
    return service.blocked(engine, state=state), None
