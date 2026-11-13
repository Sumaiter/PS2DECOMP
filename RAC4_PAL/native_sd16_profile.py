"""Offer the existing EE29 file profile for a proven native SD16 save frame.

This only adds a compiler search alternative; it neither changes C semantics
nor approves bytes, relocation targets, ABI contracts or runtime ownership.
The lead supplies a fresh successful independent ABI/profile availability probe.
"""
import re,struct,hashlib,json,os,sys,uuid,subprocess
from pathlib import Path
from auto_engine import complete_cfg
from search_rules import code_only

FILES={
 'toolchain/sn-prodg-24/local/sce/ee/gcc/bin/ee-gcc.exe':'6990e92560f4085006bc50aea249abad1236fba98f592102c0b13b21752585e3',
 'toolchain/sn-prodg-24/local/sce/ee/gcc/lib/gcc-lib/ee/2.9-ee-991111/cc1.exe':'124f7c6870b7facbaecbc51f0b87c0aad46db5f6ed491c21638e1da991e45be5',
 'toolchain/sn-prodg-24/local/sce/ee/gcc/lib/gcc-lib/ee/2.9-ee-991111/cpp.exe':'8f282abc89a6d2281e87617a7de7ebb0edbdc8dbb02d3e0a9ecc5d4825146a19',
 'toolchain/sn-prodg-24/local/sce/ee/gcc/lib/gcc-lib/ee/2.9-ee-991111/specs':'209346ef10ce7a09fc154792e649837d352eeaa588fa77e0399a4c85d4f04fca',
}
SUPPORT=('include/common.h','tools/cc.sh',
 'toolchain/sn-prodg-3.01/usr/local/sce/ee/gcc/bin/ee-gcc2953.exe',
 'toolchain/sn-prodg-3.01/usr/local/sce/ee/gcc/bin/ee-as.exe')
ABI_PROBE='''#include "common.h"
typedef char check_int[sizeof(int)==4 ? 1 : -1];
typedef char check_ptr[sizeof(void *)==4 ? 1 : -1];
typedef char check_long[sizeof(long)==8 ? 1 : -1];
typedef char check_words[sizeof(s64)==8 && sizeof(u64)==8 ? 1 : -1];
typedef char check_scalars[sizeof(s8)==1 && sizeof(u8)==1 && sizeof(s16)==2 && sizeof(u16)==2 && sizeof(s32)==4 && sizeof(u32)==4 && sizeof(f32)==4 && sizeof(f64)==8 ? 1 : -1];
s32 rac4_ee29_profile_abi_probe(void) { return 1; }
'''
_PROBES={}

def inputs(repository):
    root=Path(repository)
    return [root/name for name in (*FILES,*SUPPORT) if (root/name).is_file()]

def inspect(repository,*,probe=True,timeout=15):
    """Fresh hashes bind the process-local probe cache, never a saved boolean."""
    root=Path(repository).resolve()
    value=dict(marker='@ee29',available=False,abiVerified=False,filesVerified=False)
    try:
        hashes={}
        for name in (*FILES,*SUPPORT):
            path=root/name
            if not path.is_file():return dict(value,reason='Existing EE29 profile input missing: '+name)
            digest=hashlib.sha256(path.read_bytes()).hexdigest();hashes[name]=digest
            if name in FILES and digest!=FILES[name]:return dict(value,reason='Existing EE29 profile hash differs: '+name)
        value.update(filesVerified=True,inputHashes=hashes)
        if not probe:return dict(value,reason='ABI execution probe required.')
        key=(str(root),hashlib.sha256(ABI_PROBE.encode('utf-8')).hexdigest(),tuple(sorted(hashes.items())))
        previous=_PROBES.get(key)
        if previous:
            source=Path(previous['probeSource']);obj=Path(previous['probeObject'])
            if (source.is_file() and obj.is_file()
                    and hashlib.sha256(source.read_bytes()).hexdigest()==previous['probeSourceHash']
                    and hashlib.sha256(obj.read_bytes()).hexdigest()==previous['probeObjectHash']):return dict(previous)
        folder=root/'build/isolated/probes'/('ee29-'+uuid.uuid4().hex);folder.mkdir(parents=True)
        source=folder/'abi.c';obj=folder/'abi.o';source.write_text(ABI_PROBE,encoding='utf-8',newline='\n')
        bash=Path(r'C:\Program Files\Git\bin\bash.exe')
        env=os.environ.copy();env['PATH']=str(bash.parent)+os.pathsep+env.get('PATH','')
        command=[str(bash),'tools/cc.sh',source.relative_to(root).as_posix(),obj.relative_to(root).as_posix(),'@ee29']
        import local_process
        result=local_process.run(command,cwd=root,env=env,timeout=timeout,text=True,errors='replace')
        (folder/'compiler.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        if result.returncode or not obj.is_file():return dict(value,reason='Existing EE29 ABI compilation failed.',probe=str(folder))
        from elftools.elf.elffile import ELFFile
        with obj.open('rb') as stream:
            elf=ELFFile(stream)
            if elf.elfclass!=32 or not elf.little_endian or elf['e_machine']!='EM_MIPS':return dict(value,reason='Existing EE29 ABI probe object format differs.')
        value.update(available=True,abiVerified=True,reason='Existing EE29 compiler and common scalar/pointer ABI verified.',
                     probeSource=str(source),probeObject=str(obj),probeSourceHash=hashlib.sha256(source.read_bytes()).hexdigest(),probeObjectHash=hashlib.sha256(obj.read_bytes()).hexdigest())
        (folder/'proof.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
        if len(_PROBES)>=8:_PROBES.pop(next(iter(_PROBES)))
        _PROBES[key]=dict(value)
        return value
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:return dict(value,reason='Existing EE29 ABI probe unavailable: '+str(exc)[:240])

def native_shape(raw,start):
    if (not isinstance(raw,(bytes,bytearray)) or len(raw)<40 or len(raw)%4
            or not isinstance(start,int) or isinstance(start,bool) or start%4
            or not 0<=start<=0xffffffff-len(raw) or not complete_cfg(raw,start)):
        return None
    words=struct.unpack('<'+'I'*(len(raw)//4),raw)
    def fields(word):return word>>26,word>>21&31,word>>16&31,(word&0x7fff)-(word&0x8000)
    op,rs,rt,imm=fields(words[0]);frame=-imm
    if (op,rs,rt)!=(9,29,29) or not 48<=frame<=0x7ff0 or frame%16:return None
    if words[-2]!=0x03e00008 or fields(words[-1])!=(9,29,29,frame):return None
    saves={};restores={};seen_call=False
    for index,word in enumerate(words):
        op,rs,rt,imm=fields(word)
        if op==3 or op==0 and word&63==9:seen_call=True
        if index in (0,len(words)-1):continue
        if rt==29 and op in (8,9,24,25):return None
        if op==0 and (word>>11&31)==29 and word&63 not in (8,9):return None
        if rs!=29:continue
        if op not in (55,63):return None
        if rt not in (*range(16,24),31) or imm<0 or imm%16 or imm+8>frame:return None
        if op==63:
            if seen_call or rt in saves or imm in saves.values():return None
            saves[rt]=imm
        else:
            if rt not in saves or saves[rt]!=imm:return None
            restores.setdefault(rt,set()).add(imm)
    if (not 3<=len(saves)<=9 or 31 not in saves or set(restores)!=set(saves)
            or frame!=len(saves)*16 or sorted(saves.values())!=list(range(0,frame,16))):return None
    return len(raw),frame,tuple(sorted(saves.items()))

def eligible(source,repository,native_bodies):
    """Pre-screen every native copy before the lead incurs an ABI probe."""
    if not isinstance(source,str) or re.search(r'/\*\s*cflags:',source):return False
    clean=code_only(source)
    if re.search(r'\b(?:asm|__asm__|__attribute__|volatile|__builtin_\w+|__FILE__|__LINE__|__DATE__|__TIME__)\b',clean):return False
    directives=re.findall(r'^\s*#.*$',clean,re.M)
    if any(not re.fullmatch(r'\s*#\s*include\s*"common.h"\s*',d) for d in directives):return False
    from search_rules import Variants
    try:
        rules=Variants(repository);tree,fn=rules.parse(source)
        for *_,node in rules._nodes(fn):
            if (isinstance(node,rules.a.Cast) and isinstance(node.to_type.type,rules.a.PtrDecl)
                    and isinstance(node.expr,rules.a.Constant) and node.expr.type!='string'):
                try:value=int(re.sub(r'[uUlL]+$','',node.expr.value),0)
                except ValueError:return False
                if value:return False
    except Exception:return False
    copies=list(native_bodies)
    if not copies:return False
    shapes=[native_shape(raw,address) for raw,address in copies]
    return not any(shape is None for shape in shapes) and len(set(shapes))==1

def generate(source,repository,native_bodies,*,status):
    """native_bodies: all (native_bytes, placement_address) catalogue copies."""
    if not status.get('available') or not status.get('abiVerified') or status.get('marker')!='@ee29':return
    if not eligible(source,repository,native_bodies):return
    yield 'Originalcompiler EE29 bei belegtem SD16-Sicherungsrahmen','/* cflags: @ee29 */\n'+source.lstrip('\r\n')
