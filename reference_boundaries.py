"""Incoming direct EE control-flow evidence, independent of catalogue names.

A self-contained return window can still be a shared tail of another function.
External branches require manual boundary review; they do not prove a catalogue
error. Calls and direct tail jumps to the entry remain valid counterexamples.
Indirect entries, executable embedded data and fallthrough need separate review.
"""
from bisect import bisect_left
import hashlib
import io
from pathlib import Path
import struct


def direct_edges(raw, start):
    """Decode direct branch/jump targets from file-backed EE instructions."""
    if start < 0 or start % 4 or len(raw) % 4 or start + len(raw) > 2**32:
        raise ValueError('Unaligned or invalid executable section.')
    edges = []
    for offset, (word,) in enumerate(struct.iter_unpack('<I', raw)):
        pc = start + offset * 4
        op, rs, rt = word >> 26, (word >> 21) & 31, (word >> 16) & 31
        kind = None
        if op in (2, 3):
            target = ((pc + 4) & 0xf0000000) | ((word & 0x3ffffff) << 2)
            kind = 'call' if op == 3 else 'jump'
        elif (op in (4, 5, 20, 21)
              or op in (6, 7, 22, 23) and rt == 0
              or op == 1 and rt in (0, 1, 2, 3, 16, 17, 18, 19)
              or op in (16, 17, 18) and rs == 8 and rt in (0, 1, 2, 3)):
            # Statically impossible edges cannot be incoming entries.
            if (op in (5, 21) and rs == rt
                    or op in (7, 23) and rs == 0
                    or op == 1 and rs == 0 and rt in (0, 2, 16, 18)):
                continue
            immediate = (word & 0x7fff) - (word & 0x8000)
            target = (pc + 4 + immediate * 4) & 0xffffffff
            kind = 'call' if op == 1 and rt in (16, 17, 18, 19) else 'branch'
            always = (op in (4, 20) and rs == rt
                      or op in (6, 22) and rs == 0
                      or op == 1 and rs == 0 and rt in (1, 3))
            if kind == 'branch' and always:
                kind = 'jump'
        if kind:
            edges.append(dict(source=pc, target=target, kind=kind, word=f'{word:08X}'))
    return edges


def window_entries(edges, start, size):
    """Separate supported entry calls/tail jumps from ambiguous external edges."""
    if start < 0 or start % 4 or size <= 0 or size % 4 or start + size > 2**32:
        raise ValueError('Invalid candidate window.')
    findings, entry_calls, tail_jumps = [], 0, 0
    for edge in edges:
        if not start <= edge['target'] < start + size or start <= edge['source'] < start + size:
            continue
        if edge['target'] == start and edge['kind'] in ('call', 'jump'):
            entry_calls += edge['kind'] == 'call'
            tail_jumps += edge['kind'] == 'jump'
            continue
        findings.append(dict(edge, entry='start' if edge['target'] == start else 'interior'))
    return dict(eligible=not findings, incoming=findings,
                directEntryCalls=entry_calls, directEntryTailJumps=tail_jumps)


def inspect(engine, name):
    """Read verified reference ELFs at every placement, with hash-bound caching."""
    report = dict(eligible=False, complete=False, placements=[], problems=[],
                  limitation='Direct incoming edges only; absence is not a boundary or ABI proof.')
    cache = getattr(engine, '_incoming_boundary_cache', None)
    if cache is None:
        cache = engine._incoming_boundary_cache = {}
    for program, section, start, size in engine.all_places(name):
        row = dict(program=program, section=section, address=start, size=size, eligible=False)
        try:
            engine.assert_reference(program)
            identity = engine.reference_identity((program, section, start, size))
            path = Path(identity['path']).resolve()
            blob = path.read_bytes()
            algorithm = identity['algorithm']
            if algorithm not in ('sha1', 'sha256') or hashlib.new(algorithm, blob).hexdigest() != identity['digest'].lower():
                raise ValueError('Reference content differs from the expected placement identity.')
            digest = hashlib.sha256(blob).hexdigest()
            ee_sections = identity.get('eeSections')
            architecture_evidence = identity.get('eeSectionsEvidence')
            if (not isinstance(ee_sections, (list, tuple)) or not ee_sections
                    or not all(isinstance(s, str) and s for s in ee_sections)
                    or not isinstance(architecture_evidence, str) or not architecture_evidence.strip()):
                raise ValueError('Explicit EE section identities and architecture evidence are required.')
            cache_identity = (path, tuple(sorted(ee_sections)))
            cached = cache.get(cache_identity)
            if cached is None or cached['sha256'] != digest:
                elf = engine.ELFFile(io.BytesIO(blob))
                if (elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_MIPS'
                        or elf['e_type'] != 'ET_EXEC'):
                    raise ValueError('Expected statically placed ET_EXEC ELF32 little-endian MIPS reference.')
                sections, edges = {}, []
                for sec in elf.iter_sections():
                    if sec.name in ee_sections and sec['sh_flags'] & 4 and sec['sh_type'] != 'SHT_NOBITS' and sec['sh_size']:
                        sections[sec.name] = (sec['sh_addr'], sec['sh_size'])
                        edges.extend(dict(e, sourceSection=sec.name) for e in direct_edges(sec.data(), sec['sh_addr']))
                edges.sort(key=lambda e: e['target'])
                cached = cache[cache_identity] = dict(sha256=digest, sections=sections, edges=edges,
                                               targets=[e['target'] for e in edges])
            sec_start, sec_size = cached['sections'][section]
            if not sec_start <= start < start + size <= sec_start + sec_size:
                raise ValueError('Candidate lies outside its executable reference section.')
            left = bisect_left(cached['targets'], start)
            right = bisect_left(cached['targets'], start + size)
            row.update(window_entries(cached['edges'][left:right], start, size), referenceSha256=digest)
            if not row['eligible']:
                report['problems'].append(program + ': external non-call entry or interior entry requires manual boundary review.')
        except Exception as error:
            row['error'] = str(error)
            report['problems'].append(program + ': incoming boundary evidence unavailable: ' + str(error))
        report['placements'].append(row)
    report['complete'] = bool(report['placements']) and all('error' not in p for p in report['placements'])
    report['eligible'] = report['complete'] and all(p['eligible'] for p in report['placements'])
    return report


def apply(engine, name, technical):
    """Prevent an exact fragment from becoming an automatic technical match."""
    if not technical.get('passed'):
        return technical
    boundary = inspect(engine, name)
    return dict(technical, passed=bool(boundary['eligible']), boundaryChecks=boundary,
                problems=[*technical.get('problems', []), *boundary['problems']])
