"""Keep four automatic search candidates per function, plus referenced evidence.

Only registered automatic attempt directories can be removed. Drafts, game
sources, manual/unregistered trials, and all job/attempt logs stay untouched.
Retired receipts are exclusion hints, never reusable compiler/match evidence.
"""
from pathlib import Path
import hashlib, json, math, os, re
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from search_rules import source_key

KEEP = 4
STAMP = re.compile(r'\d{8}-\d{6}-[0-9a-f]{8}\Z')
FUNCTION = re.compile(r'func_(?:L\d\d_)?[0-9A-F]{8}\Z')
FILES = {'candidate.c', 'candidate.o', 'compiler.log', 'run.json', 'result.json'}


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    os.replace(temp, path)


def record_digest(record):
    return digest(json.dumps({k:v for k,v in record.items() if k!='_recordDigests'}, sort_keys=True, ensure_ascii=True).encode())


@lru_cache(maxsize=8)
def _workspace(root):
    root=Path(root).resolve()
    return root,(root/'build/studio').resolve()


def safe_directory(root, name, snapshot):
    root,studio = _workspace(str(root))
    if not studio.is_relative_to(root) or not FUNCTION.fullmatch(name):
        raise ValueError('Studio/function outside the intended repository.')
    path = Path(snapshot)
    if path.name != 'candidate.c' or path.is_symlink():
        raise ValueError('Not a plain candidate snapshot.')
    directory = path.parent
    resolved=directory.resolve()
    if (not STAMP.fullmatch(directory.name) or directory.is_symlink()
            or getattr(directory, 'is_junction', lambda:False)()
            or resolved.parent != studio/name):
        raise ValueError('Snapshot directory escaped its exact function workspace.')
    return resolved


def index_path(root, name):
    if not FUNCTION.fullmatch(name):
        raise ValueError('Invalid function name.')
    return Path(root)/'build/retention'/f'{name}.json'


def load(root, name):
    path=index_path(root,name)
    return read(path) if path.is_file() else dict(schema=1,name=name,keep=KEEP,candidates={},protected=[],retired={})


def rank(record):
    score=record.get('score',-1)
    if type(score) not in (int,float) or not math.isfinite(score):score=-1
    return (record.get('technicalMatch') is True,score,str(record.get('snapshot','')))


def job_directories(root,name=None):
    """Current sessions and copied retry packets; no recursive snapshot walk."""
    directory=Path(root)/'build/autodecomp'
    if not directory.exists():return
    for session in directory.iterdir():
        if not session.is_dir():continue
        bases=[session]
        history=session/'history'
        if history.is_dir():bases.extend(p for p in history.iterdir() if p.is_dir())
        for base in bases:
            jobs=base/'jobs'
            if jobs.is_dir():
                if name is not None:
                    folder=jobs/name
                    if folder.is_dir():yield folder
                    continue
                for folder in jobs.iterdir():
                    if folder.is_dir() and FUNCTION.fullmatch(folder.name):yield folder


def evidence_stamps(root):
    """Keep explicitly referenced review/compiler proofs at their original paths."""
    root=Path(root); stamps=set();files=[]
    build=root/'build'
    files.extend(build.glob('*.json'))
    for pattern in ('review_*','parallel*'):
        for directory in build.glob(pattern):
            if directory.is_dir():
                for parent,dirs,names in os.walk(directory,followlinks=False):
                    dirs[:]=[d for d in dirs if d not in {'before','history','build','venv','node_modules','.git','__pycache__'}
                             and not d.startswith('pre-upstream-')
                             and not Path(parent,d).is_symlink()
                             and not getattr(Path(parent,d),'is_junction',lambda:False)()]
                    files.extend(Path(parent,n) for n in names if Path(n).suffix in ('.json','.md','.py'))
    # Buildable-probe snapshots are not automatic attempt directories and are
    # never candidates for deletion; their cache does not need rescanning here.
    cache_path=build/'retention/protected-reference-cache.json'
    try:previous=read(cache_path).get('files',{})
    except (OSError,ValueError,TypeError):previous={}
    def scan(path):
        # Bulk read/check ledgers are retained as hash history, not a request to
        # retain every discarded experiment forever. Individual proofs remain.
        if path.name=='protected-inputs.json' or 'read-history' in path.name:return None
        try:
            stat=path.stat();key=str(path);signature=[stat.st_size,stat.st_mtime_ns]
            old=previous.get(key,{})
            if old.get('signature')==signature:return key,old
            found=sorted(set(re.findall(r'\b\d{8}-\d{6}-[0-9a-f]{8}\b',path.read_text(encoding='utf-8'))))
            return key,dict(signature=signature,stamps=found)
        except (OSError,UnicodeError):
            # A disappearing old proof cannot make its cached references unsafe.
            return (str(path),previous[str(path)]) if str(path) in previous else None
    entries={}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for value in pool.map(scan,files):
            if value:
                key,entry=value;entries[key]=entry;stamps.update(entry['stamps'])
    write(cache_path,dict(schema=1,files=entries))
    return stamps


def _packet(folder):
    try:
        job=read(folder/'job.json')
        pins=[v['snapshot'] for key in ('best','lastAttempt')
              if (v:=job.get(key) or {}).get('snapshot') and (key=='best' or not job.get('best'))]
    except (OSError,ValueError,TypeError):pins=['function:'+folder.name]
    log=folder/'attempts.jsonl'
    try:lines=log.read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:lines=[]
    except (OSError,UnicodeError):lines=[];pins.append('function:'+folder.name)
    return folder.name,pins,lines


def inventory(root,progress=None):
    groups={};protected=set();stamps=evidence_stamps(root)
    if progress:progress('References collected; reading job packets')
    def packets():
        # Bounded batches avoid retaining the full copied history in RAM.
        folders=iter(job_directories(root))
        with ThreadPoolExecutor(max_workers=12) as pool:
            while batch:=list(islice(folders,512)):
                yield from pool.map(_packet,batch)
    for checked,(name,pins,lines) in enumerate(packets(),1):
        if progress and checked%2000==0:progress(f'Read {checked} job packets')
        protected.update(pins)
        for line in lines:
            try:
                record=json.loads(line);snapshot=record['snapshot']
                path=Path(snapshot)
                if (path.name!='candidate.c' or not STAMP.fullmatch(path.parent.name)
                        or path.parent.parent!=Path(root)/'build/studio'/name):continue
                bucket=groups.setdefault(name,{})
                fingerprints=set((bucket.get(snapshot) or {}).get('_recordDigests',[]))
                fingerprints.add(record_digest(record))
                if snapshot not in bucket or rank(record)>rank(bucket[snapshot]):bucket[snapshot]=dict(record)
                bucket[snapshot]['_recordDigests']=sorted(fingerprints)
            except (OSError,ValueError,KeyError,TypeError):continue
    return groups,protected,stamps


def prepare(root,name,records,protected=(),stamps=()):
    """Build an explicit, hashed deletion plan without modifying anything."""
    state=load(root,name)
    local_protected={p for p in protected if Path(p).parent.parent.name==name}
    state['protected']=sorted(set(state.get('protected',[]))|local_protected)
    for record in records:
        snapshot=record['snapshot'];safe_directory(root,name,snapshot)
        previous=state['candidates'].get(snapshot)
        fingerprints=set((previous or {}).get('_recordDigests',[]))
        if previous is not None:fingerprints.add(record_digest(previous))
        fingerprints.update(record.get('_recordDigests',[]));fingerprints.add(record_digest(record))
        if previous is None or rank(record)>rank(previous):state['candidates'][snapshot]=dict(record)
        state['candidates'][snapshot]['_recordDigests']=sorted(fingerprints)
    present={p:r for p,r in state['candidates'].items() if Path(p).is_file()}
    ordered=sorted(present,key=lambda p:rank(present[p]),reverse=True)
    # Identical source snapshots do not consume all four slots.
    top=[];hashes=set()
    for p in ordered:
        identity=present[p].get('sourceHash') or p
        if identity in hashes:continue
        hashes.add(identity);top.append(p)
        if len(top)==KEEP:break
    kept=set(top)|set(state['protected'])
    if 'function:'+name in protected:
        kept.update(present)
        state['protected']=sorted(set(state['protected'])|set(present))
    kept.update(p for p in present if Path(p).parent.name in stamps)
    state['protected']=sorted(set(state['protected'])|{p for p in present if Path(p).parent.name in stamps})
    deletions=[];skipped=[]
    from auto_jobs import interrupted_build,definite_source_error
    for snapshot,record in present.items():
        if snapshot in kept:continue
        try:
            directory=safe_directory(root,name,snapshot)
            paths=list(directory.iterdir())
            if not paths or any(not p.is_file() or p.is_symlink() or p.name not in FILES for p in paths):
                raise ValueError('Unknown files/links in snapshot; preserve the directory.')
            blobs={p.name:p.read_bytes() for p in paths}
            if digest(blobs['candidate.c'])!=record['sourceHash']:raise ValueError('Source hash changed.')
            result=json.loads(blobs['result.json'].decode('utf-8'))
            if (result.get('snapshot')!=snapshot or result.get('sourceHash')!=record['sourceHash']
                    or result.get('name')!=name):raise ValueError('Source/result identity changed.')
            if 'compiledSize' in result and (not record.get('objectHash') or digest(blobs['candidate.o'])!=record['objectHash']):
                raise ValueError('Object hash changed.')
            text=Path(snapshot).read_text(encoding='utf-8')
            negative=(not record.get('technicalMatch') and not interrupted_build(result,record)
                      and (type(result.get('compiledSize')) is int and result['compiledSize']>0 or definite_source_error(result)))
            receipt=dict(snapshot=snapshot,sourceHash=record['sourceHash'],objectHash=record.get('objectHash'),
                         resultHash=digest(blobs['result.json']),recordDigest=record_digest(record),
                         recordDigests=record.get('_recordDigests',[record_digest(record)]),
                         sourceKey=source_key(text),sourceTextHash=digest(text.encode()),negative=bool(negative),
                         compilerEvidenceAvailable=False)
            deletions.append(dict(directory=str(directory),files={str(directory/f):digest(b) for f,b in blobs.items()},
                                  bytes=sum(map(len,blobs.values())),receipt=receipt))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            skipped.append(dict(snapshot=snapshot,reason=str(exc)))
    return dict(name=name,state=state,top=top,protected=sorted(set(present)&kept-set(top)),
                deletions=deletions,skipped=skipped)


def apply(root,plan):
    """Recheck exact paths/hashes before deleting only the planned generated files."""
    state=plan['state'];name=plan['name'];removed=0;size=0
    state['searchCandidates']=plan['top'];state['protectedEvidence']=plan['protected']
    for item in plan['deletions']:
        directory=safe_directory(root,name,item['receipt']['snapshot'])
        if str(directory)!=item['directory']:raise ValueError('Deletion target changed.')
        current=list(directory.iterdir())
        if {str(p) for p in current}!=set(item['files']):raise ValueError('Directory contents changed.')
        for p in current:
            if p.is_symlink() or not p.is_file() or digest(p.read_bytes())!=item['files'][str(p)]:
                raise ValueError('Planned artifact changed; deletion stopped.')
        # Receipt is durable BEFORE deletion. It cannot supply a compiler hit.
        state['retired'][item['receipt']['snapshot']]=item['receipt']
        state['candidates'].pop(item['receipt']['snapshot'],None)
        write(index_path(root,name),state)
        for p in current:p.unlink()
        directory.rmdir();removed+=1;size+=item['bytes']
    write(index_path(root,name),state)
    return dict(removedSnapshots=removed,freedBytes=size,keptCandidates=len(plan['top']),
                additionalProtectedEvidence=len(plan['protected']),skipped=len(plan['skipped']))


def retired_key(root,name,record,receipts):
    """Only a bound negative-search hint; deleted objects are never cache hits."""
    receipt=receipts.get(record.get('snapshot'))
    if not receipt or not receipt.get('negative') or receipt.get('compilerEvidenceAvailable') is not False:return None
    try:
        safe_directory(root,name,record['snapshot'])
        if (record_digest(record) not in receipt.get('recordDigests',[receipt['recordDigest']]) or receipt['sourceHash']!=record['sourceHash']
                or receipt.get('objectHash')!=record.get('objectHash')):return None
        return receipt['sourceKey'] if re.fullmatch(r'[0-9a-f]{64}',receipt['sourceKey']) else None
    except (ValueError,KeyError,TypeError):return None


def finish(root,name,directory,job):
    """Compact after a worker has finished, never while its frontier is in use."""
    from auto_jobs import file_lock
    records=[]
    protected=[]
    if not index_path(root,name).is_file():
        # A function excluded from an earlier cleanup because it was running
        # still joins the same global four slots when a later worker finishes.
        for old in job_directories(root,name):
            try:
                earlier=read(old/'job.json');best=earlier.get('best') or earlier.get('lastAttempt') or {}
                if best.get('snapshot'):protected.append(best['snapshot'])
                for line in (old/'attempts.jsonl').read_text(encoding='utf-8').splitlines():
                    record=json.loads(line);safe_directory(root,name,record['snapshot']);records.append(record)
            except (OSError,ValueError,KeyError,TypeError):
                # A missing/corrupt historical best must not be discarded.
                return dict(skippedReason='Historical job evidence unreadable; retained all candidates.')
    for line in (Path(directory)/'attempts.jsonl').read_text(encoding='utf-8').splitlines():
        record=json.loads(line)
        try:safe_directory(root,name,record['snapshot'])
        except (ValueError,KeyError):continue
        records.append(record)
    if not records:return None
    best=job.get('best') or job.get('lastAttempt') or {}
    if best.get('snapshot'):protected.append(best['snapshot'])
    with file_lock(index_path(root,name).with_suffix('.guard')):
        # Legacy cleanup records durable review references once. Newly handed
        # off bests are pinned here, and never silently dropped in later runs.
        path=Path(root)/'build/retention/protected-stamps.json'
        stamps=read(path).get('stamps',[]) if path.is_file() else evidence_stamps(root)
        plan=prepare(root,name,records,protected=protected,stamps=stamps)
        return apply(root,plan)


def refresh_protection(root):
    """Refresh once before launching a session, not for each attempt/worker."""
    stamps=evidence_stamps(root)
    write(Path(root)/'build/retention/protected-stamps.json',dict(schema=1,stamps=sorted(stamps)))
    return stamps
