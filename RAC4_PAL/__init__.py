"""Game-specific PAL integration. No game data or proprietary tools."""
import hashlib
from pathlib import Path
from types import MethodType


def version_availability(repository):
    from .engine import AutoEngine
    return AutoEngine.version_availability(repository)


def create_engine(repository, settings, version=None):
    from .engine import AutoEngine
    from .inputs import pin_inputs
    engine = AutoEngine(repository, version=version or settings['version'])
    engine.pin_inputs = MethodType(lambda self, names: pin_inputs(self, names), engine)
    def environment(self):
        pins = self.pin_inputs(list(self.places))
        return hashlib.sha256(''.join(p['sha256'] for _, p in sorted(pins.items())).encode()).hexdigest()
    engine.environment = MethodType(environment, engine)
    def validated_gp(self, program):
        # Preserve the original adapter's exact startup-sequence evidence.
        self.assert_reference('main')
        import struct
        raw = self.check.disc_bytes(('main', 'core.text', 0x157678, 0x2c))
        words = [struct.unpack_from('<I', raw, off)[0] for off in (0, 0x14, 0x28)]
        if words[0] & 0xffff0000 != 0x3c040000 or words[1] & 0xffff0000 != 0x24840000 or words[2] != 0x0080e02d:
            raise ValueError('RAC4 PAL startup GP evidence differs.')
        return ((words[0] & 0xffff) << 16) + ((words[1] & 0x7fff) - (words[1] & 0x8000))
    engine.validated_gp = MethodType(validated_gp, engine)
    return engine


def coordination_state(engine):
    return _coordination_state(engine, discovery=False)


def candidate_coordination_state(engine):
    """List free candidates without granting permission to begin matching."""
    return _coordination_state(engine, discovery=True)


def _coordination_state(engine, *, discovery):
    from . import public_reservations as service, upstream_matches
    from auto_jobs import PlacementIndex, public_aliases
    snapshot = service.fetch_reservations(engine.root)
    account, _ = service._api('GET', 'user')
    login = account.get('login', '').casefold()
    if not login:
        raise ValueError('Cannot establish the current contributor identity.')
    own = {e['name'] for e in snapshot['entries'] if e['owner'].casefold() == login
           and e['version'] == engine.version and e['status'] in ('active', 'verified')}
    foreign = dict(snapshot, entries=[e for e in snapshot['entries'] if e['owner'].casefold() != login
                                    or e['name'] not in own or e['version'] != engine.version])
    aliases = public_aliases(engine)
    held = set(service.blocked_for_version(foreign, engine.version, aliases=aliases))
    if discovery:
        # Verified results awaiting integration are not new matching work,
        # including when they belong to the current contributor.
        pending = dict(snapshot, entries=[e for e in snapshot['entries']
                                         if e['owner'].casefold() == login and e['status'] == 'verified'])
        held.update(service.blocked_for_version(pending, engine.version, aliases=aliases))
    # No original user's identity, comment ID or posting permission is inherited.
    upstream = upstream_matches.fetch_matches(engine.version)
    index = PlacementIndex(engine, ({str(i): {'places': r.places} for i, r in enumerate(upstream.matches)}, set(), {}, {}))
    held |= {n for n in engine.places if n in upstream.names or index.overlaps(engine.all_places(n), n)}
    # The read-only list can include unreserved functions. Session creation,
    # resume and manual compilation still require the current public scope.
    return held, None if discovery else own
