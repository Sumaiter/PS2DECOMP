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


def workflow(engine):
    value = engine.config.get('coordination', {}).get('workflow', 'public-first')
    if value not in ('public-first', 'local-first'):
        raise ValueError('RAC4 coordination.workflow must be public-first or local-first.')
    return value


def assert_publication_ready(engine, names):
    """Require a fresh public scope for selected results, even in local-first."""
    names = tuple(names or ())
    if not names:
        return
    unknown = [name for name in names if not isinstance(name, str) or name not in engine.places]
    if unknown:
        raise ValueError('RAC4: Unknown selected functions: ' + ', '.join(map(str, unknown)))
    held, own = _coordination_state(engine, discovery=False, publication=True, selected=names)
    conflicts = set(names) & held
    if conflicts:
        raise ValueError('RAC4: Publicly reserved, integrated or unresolved: ' + ', '.join(sorted(conflicts)))
    outside = set(names) - own
    if outside:
        folder = engine.root / 'build/coordination'
        folder.mkdir(parents=True, exist_ok=True)
        proposal = folder / 'reservation-comment-proposal.txt'
        proposal.write_text('Version: PAL (SCES_532.85)\nStatus: active\n'
                            'Selected PR results; add only after checking the current contributor comment.\n\n'
                            + ''.join('- ' + name + '\n' for name in sorted(outside)), encoding='utf-8', newline='\n')
        raise ValueError('RAC4 PR: Selected results need an own public reservation in issue #112. '
                         'No comment was posted. Local proposal: ' + str(proposal))


def _coordination_state(engine, *, discovery, publication=False, selected=()):
    if engine.config.get('coordination', {}).get('mode') != 'native':
        raise ValueError('RAC4 requires its native coordination gate.')
    selected_workflow = workflow(engine)
    from . import public_reservations as service, upstream_matches
    from auto_jobs import PlacementIndex, public_aliases
    snapshot = service.fetch_reservations(engine.root)
    account, _ = service._api('GET', 'user')
    login = account.get('login', '').casefold()
    if not login:
        raise ValueError('Cannot establish the current contributor identity.')
    if publication:
        reserved = {(e['version'], e['name']) for e in snapshot['entries']
                    if e['owner'].casefold() == login and e['status'] != 'released'}
        reserved.update((engine.version, name) for name in selected)
        if len(reserved) > 500:
            raise ValueError('RAC4: At most 500 unfinished functions may be publicly reserved across all scopes. '
                             'Select fewer results or release finished/dropped reservations first.')
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
    # Local-first is an explicitly selected workspace policy. Public scope is
    # still mandatory for the independently reviewed results selected for a PR.
    local = selected_workflow == 'local-first' and not publication
    return held, None if discovery or local else own
