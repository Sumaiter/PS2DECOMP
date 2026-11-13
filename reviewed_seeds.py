"""Freeze explicitly exported open review candidates as additional search roots.

This module neither claims functions nor builds code. Old search inputs and
editor drafts are never modified. A seed is search input, never semantic approval.
"""
from pathlib import Path
import hashlib
import io
import json
import re

from elftools.elf.elffile import ELFFile
import retry_context
import source_fidelity
from search_rules import Variants


FUNCTION = re.compile(r'func_(?:L\d\d_)?[0-9A-F]{8}\Z')
SESSION = re.compile(r'[0-9a-f]{32}\Z')
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
FILENAMES = ('candidate.c', 'candidate.o', 'result.json', 'checks.json', 'entry.json')


def _sha(blob):
    return hashlib.sha256(blob).hexdigest()


def _json(blob):
    value = json.loads(blob.decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object.')
    return value


def _check(condition, reason):
    if not condition:
        raise ValueError(reason)


def _hash(blob, expected, label):
    _check(isinstance(expected, str) and DIGEST.fullmatch(expected) and _sha(blob) == expected,
           label + ' hash missing or changed.')


def _paths(root, name, entry):
    """Only the known studio snapshot and explicit local review report are read."""
    snapshot = Path(entry['snapshot']).resolve()
    studio = (root / 'build/studio' / name).resolve()
    _check(snapshot.name == 'candidate.c' and snapshot.parent.parent == studio,
           'Snapshot must be a direct studio candidate for this function.')
    report = Path(entry['report']).resolve()
    relative = report.relative_to(root)
    _check(len(relative.parts) >= 4 and relative.parts[0] == 'build'
           and re.fullmatch(r'review_auto[A-Za-z0-9_-]+', relative.parts[1])
           and relative.parts[-1] == 'checks.json', 'Report must be local review_auto*/.../checks.json.')
    return snapshot, report


def _ast(source, root, name):
    fidelity = source_fidelity.inspect(source, root)
    _check(fidelity.get('astAnalyzed') is True and fidelity.get('violations') == [],
           'Source fidelity or AST validation remains open.')
    rules = Variants(root)
    tree, fn = rules.parse(source)
    _check(fn.decl.name == name, 'Source definition has a different function name.')
    a = rules.a
    # Forward declarations and typedefs are permitted; new file-scope storage is not.
    for node in tree.ext:
        if isinstance(node, a.Decl) and not isinstance(node.type, a.FuncDecl):
            _check(node.name is None or ('extern' in node.storage and node.init is None),
                   'Additional file-scope storage definition.')
    for *_, node in rules._nodes(fn.body):
        if isinstance(node, a.Decl):
            _check('static' not in node.storage, 'Static local storage needs separate ownership review.')


def _object(blob, name):
    elf = ELFFile(io.BytesIO(blob))
    _check(elf.elfclass == 32 and elf.little_endian and elf['e_machine'] == 'EM_MIPS'
           and elf['e_type'] == 'ET_REL', 'Object is not a 32-bit little-endian MIPS relocatable.')
    symbols = elf.get_section_by_name('.symtab')
    _check(symbols is not None, 'Object symbol table missing.')
    defined = [s for s in symbols.iter_symbols() if isinstance(s['st_shndx'], int)]
    functions = [s for s in defined if s['st_info']['type'] == 'STT_FUNC']
    _check(len(functions) == 1 and functions[0].name == name,
           'Additional or mismatched object function definitions.')
    fn = functions[0]
    _check(fn['st_shndx'] > 0, 'Function is not defined in a code section.')
    text = elf.get_section(fn['st_shndx'])
    _check(text['sh_flags'] & 6 == 6 and fn['st_value'] == 0
           and fn['st_size'] == text['sh_size'] and fn['st_size'] > 0
           and fn['st_size'] % 4 == 0 and len(text.data()) == fn['st_size'],
           'Object does not contain one complete allocated code window.')
    allocated = [i for i, s in enumerate(elf.iter_sections()) if s['sh_flags'] & 2 and s['sh_size']]
    _check(allocated == [fn['st_shndx']], 'Additional allocated object data or code.')
    for symbol in symbols.iter_symbols():
        if symbol['st_shndx'] == 'SHN_UNDEF':
            continue
        _check(symbol['st_info']['type'] != 'STT_OBJECT'
               and not (symbol['st_info']['bind'] == 'STB_GLOBAL' and symbol.name != name),
               'Additional object or global definition.')
    return int(fn['st_size'])


def _placements(report):
    rows = report.get('placements')
    _check(isinstance(rows, list) and rows, 'Complete placement evidence missing.')
    result = {}
    for row in rows:
        key = (row['program'], row['section'], row['address'], row['size'])
        _check(isinstance(key[0], str) and key[0] and isinstance(key[1], str)
               and type(key[2]) is int and key[2] >= 0
               and type(key[3]) is int and key[3] > 0 and key[3] % 4 == 0,
               'Malformed placement identity.')
        digest = row.get('referenceHash')
        _check(key not in result and isinstance(digest, str) and DIGEST.fullmatch(digest),
               'Duplicate placement or invalid reference hash.')
        result[key] = digest
    return result


def _validate(root, name, entry, blobs):
    _check(isinstance(entry, dict) and entry.get('reviewedOpenSearchSeed') is True,
           'No explicit reviewed-open search export.')
    _check(entry.get('name', name) == name and entry.get('version', 'pal') == 'pal',
           'Wrong function or region in review export.')
    _check(isinstance(entry.get('reviewedBy'), str) and entry['reviewedBy'].strip(), 'Reviewer missing.')
    questions = entry.get('openQuestions')
    _check(isinstance(questions, list) and questions
           and all(isinstance(q, str) and q.strip() for q in questions), 'Explicit open questions missing.')
    _check(isinstance(entry.get('originalSession'), str) and SESSION.fullmatch(entry['originalSession']),
           'Invalid original session identity.')
    snapshot, _ = _paths(root, name, entry)
    for file, field in [('candidate.c', 'sourceHash'), ('candidate.o', 'objectHash'),
                        ('result.json', 'resultHash'), ('checks.json', 'reportHash')]:
        _hash(blobs[file], entry.get(field), file)
    # Match Path.read_text's universal-newline convention. Raw copied bytes and
    # their provenance hashes remain untouched, including Windows CRLF files.
    source = io.TextIOWrapper(io.BytesIO(blobs['candidate.c']), encoding='utf-8', newline=None).read()
    _check(0 < len(blobs['candidate.c']) <= 256_000, 'Invalid source size.')
    result, report = _json(blobs['result.json']), _json(blobs['checks.json'])
    _check(result.get('name') == name and result.get('sourceHash') == entry['sourceHash']
           and Path(result['snapshot']).resolve() == snapshot, 'Result/source/function binding differs.')
    _check(report.get('name') == name and report.get('sourceHash') == entry['sourceHash']
           and Path(report['source']).resolve() == snapshot and report.get('objectHash') == entry['objectHash']
           and report.get('sourceVerified') is True and report.get('objectVerified') is True
           and report.get('manualReviewRequired') is True, 'Report/source/object binding is unverified.')
    _check(report.get('technicalPassed') is False, 'Technical matches belong in full review, not open-seed export.')
    diagnostics = report.get('placementDiagnostics')
    _check(isinstance(diagnostics, list) and all(isinstance(d, dict) and d.get('kind') == 'instruction-sequence'
                                                for d in diagnostics), 'Known incorrect or unresolved target binding.')
    types = report.get('types', {})
    _check(isinstance(types, dict) and not types.get('unresolvedMarkers')
           and not types.get('contractWarnings') and not types.get('snapshotContractWarnings'),
           'Known type, ABI or unresolved-source conflict.')
    _check(not report.get('problems'), 'Review report contains additional object/evidence problems.')
    _placements(report)
    _ast(source, root, name)
    size = _object(blobs['candidate.o'], name)
    _check(type(result.get('compiledSize')) is int and result['compiledSize'] == size,
           'Compiled result size does not match the bound object.')
    # Use the same public compiler flag gate, without invoking the backend.
    from backend import SAFE_FLAGS
    flags = next((m.group(1).split() for line in source.splitlines()[:3]
                  if (m := re.match(r'^/\* cflags: (.*) \*/', line))), [])
    _check(all(flag in SAFE_FLAGS for flag in flags) and result.get('flags', []) == flags,
           'Compiler flags are unsupported or do not match the compiled snapshot.')
    _check(('@ee29-01' not in flags or flags == ['@ee29-01', '-O2', '-G0', '-Wa,-G0'])
           and ('@ee29-01' in flags or not set(flags) & {'-O2', '-G0', '-Wa,-G0'}),
           'Mixed or incomplete original compiler profile.')
    return source, report


def capture(root, name, directory, registry):
    """Copy an explicitly exported candidate into a new retry, or fail closed.

    The caller has already atomically claimed and revalidated the function. The
    registry is an immutable in-memory schema1 PAL value, never read here.
    """
    if registry is None:
        return None
    try:
        _check(isinstance(registry, dict) and type(registry.get('schema')) is int and registry['schema'] == 1
               and registry.get('version') == 'pal' and isinstance(registry.get('entries'), dict),
               'Unsupported reviewed-seed registry schema or version.')
        entry = registry['entries'].get(name)
        if entry is None:
            return None
        _check(isinstance(name, str) and FUNCTION.fullmatch(name), 'Invalid function name.')
        root, directory = Path(root).resolve(), Path(directory).resolve()
        _check(directory.name == name and directory.parent.name == 'jobs'
               and SESSION.fullmatch(directory.parent.parent.name)
               and directory.parent.parent.parent == (root / 'build/autodecomp').resolve(),
               'Destination is not the new session job directory.')
        # Canonicalize by JSON roundtrip so caller mutations cannot affect capture.
        entry = _json(json.dumps(entry, ensure_ascii=False).encode('utf-8'))
        snapshot, report_path = _paths(root, name, entry)
        blobs = {file: path.read_bytes() for file, path in (
            ('candidate.c', snapshot), ('candidate.o', snapshot.with_suffix('.o')),
            ('result.json', snapshot.with_name('result.json')), ('checks.json', report_path))}
        _validate(root, name, entry, blobs)
        original = root / 'build/autodecomp' / entry['originalSession']
        session = _json((original / 'session.json').read_bytes())
        job_path = original / 'jobs' / name / 'job.json'
        job_bytes = job_path.read_bytes(); job = _json(job_bytes)
        _check(session.get('id') == entry['originalSession'] and name in session.get('names', [])
               and job.get('name') == name and job.get('status') == 'handed_off' and job.get('handedOff')
               and not job.get('deferred') and not job.get('blocked'),
               'Original application result was not explicitly handed off or is deferred.')
        blobs['entry.json'] = json.dumps(entry, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'
        destination = directory / 'review-root'
        destination.mkdir(parents=True, exist_ok=False)
        files = {}
        for file in FILENAMES:
            path = destination / file
            path.write_bytes(blobs[file]); files[str(path)] = _sha(blobs[file])
        return dict(schema=1, version='pal', name=name, state='prepared', files=files,
                    snapshot=str(destination / 'candidate.c'), sourceHash=entry['sourceHash'],
                    objectHash=entry['objectHash'], resultHash=entry['resultHash'],
                    report=str(destination / 'checks.json'), reportHash=entry['reportHash'],
                    entry=str(destination / 'entry.json'), entryHash=_sha(blobs['entry.json']),
                    originalSession=entry['originalSession'], originalJobHash=_sha(job_bytes),
                    reviewedBy=entry['reviewedBy'], openQuestions=entry['openQuestions'],
                    semanticApproval=False, manualReviewRequired=True)
    except Exception as exc:
        return dict(state='rejected', reason=str(exc)[:1000], semanticApproval=False)


def plan(engine, folder, data, job, name):
    """Validate only frozen session input and current retail, never external exports."""
    try:
        _check(isinstance(name, str) and FUNCTION.fullmatch(name), 'Invalid function name.')
        metadata = job.get('reviewedSeed')
        _check(isinstance(metadata, dict) and metadata.get('state') == 'prepared', 'No prepared reviewed-open seed.')
        _check(type(metadata.get('schema')) is int and metadata['schema'] == 1 and metadata.get('version') == 'pal'
               and metadata.get('name') == name and job.get('name') == name, 'Wrong prepared seed identity or region.')
        _check(retry_context.origin(Path(folder), data, job, name) is not None,
               'No verified automatic origin; editor or unknown lineage remains unchanged.')
        root, folder = Path(engine.root).resolve(), Path(folder).resolve()
        _check(folder.parent == (root / 'build/autodecomp').resolve() and SESSION.fullmatch(folder.name),
               'Prepared seed is outside this session.')
        destination = (folder / 'jobs' / name / 'review-root').resolve()
        _check(destination.is_relative_to(folder)
               and destination.parent == (folder / 'jobs' / name).resolve(), 'Prepared seed path escaped the job.')
        expected = {str(destination / file) for file in FILENAMES}
        files = metadata.get('files')
        _check(isinstance(files, dict) and set(files) == expected, 'Incomplete or external frozen file list.')
        _check(metadata.get('snapshot') == str(destination / 'candidate.c')
               and metadata.get('report') == str(destination / 'checks.json')
               and metadata.get('entry') == str(destination / 'entry.json'), 'Frozen metadata path differs.')
        blobs = {}
        for file in FILENAMES:
            path = destination / file
            _check(path.resolve().parent == destination, 'Frozen file is linked outside the snapshot.')
            blob = path.read_bytes(); _hash(blob, files[str(path)], file); blobs[file] = blob
        entry = _json(blobs['entry.json'])
        for field in ('sourceHash', 'objectHash', 'resultHash', 'reportHash', 'originalSession'):
            _check(metadata.get(field) == entry.get(field), 'Frozen export metadata differs: ' + field)
        _hash(blobs['entry.json'], metadata.get('entryHash'), 'Frozen manifest')
        source, report = _validate(root, name, entry, blobs)
        expected_places = _placements(report)
        current = {}
        checked = set()
        for place in engine.all_places(name):
            key = tuple(place[:4])
            _check(key not in current, 'Current catalogue has duplicate placements.')
            if key[0] not in checked:
                engine.assert_reference(key[0]); checked.add(key[0])
            raw = engine.check.disc_bytes(key)
            _check(len(raw) == key[3], 'Current retail placement size differs.')
            current[key] = _sha(raw)
        _check(current and current == expected_places, 'Current retail placement set or reference hashes changed.')
        return dict(eligible=True, reason='Explicit reviewed-open source with frozen provenance and current references.',
                    source=source, sourceHash=metadata['sourceHash'], snapshot=metadata['snapshot'],
                    sourceTextHash=_sha(source.encode('utf-8')),
                    reviewedBy=entry['reviewedBy'], openQuestions=entry['openQuestions'],
                    semanticApproval=False, manualReviewRequired=True)
    except Exception as exc:
        return dict(eligible=False, reason=str(exc)[:1000], semanticApproval=False)
