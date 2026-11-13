"""Optional real-compiler checks on synthetic C, without ROMs or game sources.

--command-json supplies an argv array with {source}/{object} placeholders.
For example a project's unchanged compiler wrapper; no shell string is run.
Outputs stay local. This is not a retail function or semantic-match audit.
"""
from pathlib import Path
import argparse
import hashlib
import io
import json
import os
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from search_rules import Variants
from scalar_definition_retarget import children
from postcall_integer_default import generate as defaults
from RAC4_PAL.native_local_after_call import generate as native_lifetime


def sha(blob):return hashlib.sha256(blob).hexdigest()


def object_info(path):
    blob=path.read_bytes();elf=ELFFile(io.BytesIO(blob))
    if elf.elfclass!=32 or not elf.little_endian or elf['e_machine']!='EM_MIPS' or elf['e_type']!='ET_REL':
        raise ValueError('Expected a little-endian ELF32 MIPS object.')
    code=elf.get_section_by_name('.text').data();rels=[]
    for section in elf.iter_sections():
        if not isinstance(section,RelocationSection):continue
        table=elf.get_section(section['sh_link'])
        for rel in section.iter_relocations():
            symbol=table.get_symbol(rel['r_info_sym'])
            rels.append(dict(section=elf.get_section(section['sh_info']).name,offset=rel['r_offset'],
                             type=rel['r_info_type'],symbol=symbol.name,symbolValue=symbol['st_value']))
    return dict(objectHash=sha(blob),textHash=sha(code),bytes=len(code),relocations=rels)


def examples(header):
    a='#include "common.h"\nReal example(Real x){Real t;Real acc;t=x;acc=t*t;return acc;}'
    b='#include "common.h"\nStatus callback(Status n);Status example(Status n){Status r;r=-542;if(callback(n)>=0)r=n;return r;}'
    d='#include "common.h"\nStatus example(int n,void *p){if(n)return (Status)0;return *(Status *)p;}'
    c='#include "common.h"\nStatus callback(void);Status example(void){Status r;Status value;value=-7;r=callback();return value+r;}'
    rules=Variants(header)
    native=struct.pack('<8I',0x27bdfff0,0xffbf0000,0x0c000800,0,0x2406fff9,0xdfbf0000,0x03e00008,0x27bd0010)
    n=native_lifetime(rules,c,[dict(raw=native,start=0x1000,symbols={'callback':0x2000})])
    joined=[text for label,text in rules.generate(d) if label=='terminale-rueckgaben-verbinden']
    variants=[('scalar-retarget',a,children(a,rules)),('integer-default',b,list(defaults(b,header))),
              ('terminal-load',d,[('join',text) for text in joined]),('pal-native-lifetime',c,[n] if n else [])]
    for label,before,after in variants:
        if len(after)!=1:raise ValueError('Expected exactly one bounded child: '+label)
        yield label,before,after[0][1]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--command-json',type=Path,required=True)
    ap.add_argument('--cwd',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();cwd=args.cwd.resolve();out=args.output.resolve()
    command=json.loads(args.command_json.read_text(encoding='utf-8'))
    if not isinstance(command,list) or not command or not all(isinstance(p,str) for p in command):raise ValueError('Compiler command must be an argv array.')
    if not all(any('{'+key+'}' in part for part in command) for key in ('source','object')):raise ValueError('Missing source/object placeholders.')
    out.mkdir(parents=True,exist_ok=False)
    results=[]
    for context,real in [('float-alias','float'),('double-alias','double')]:
        root=out/context;(root/'include').mkdir(parents=True)
        header='typedef '+real+' Real;\ntypedef int Status;\n'
        (root/'include/common.h').write_text(header,encoding='utf-8')
        # Quoted common.h is resolved beside each synthetic translation unit.
        (root/'common.h').write_text(header,encoding='utf-8')
        for label,before,after in examples(root):
            pair=[]
            for phase,source in [('before',before),('after',after)]:
                path=root/(label+'-'+phase+'.c');obj=path.with_suffix('.o')
                path.write_text(source,encoding='utf-8')
                values={'source':Path(os.path.relpath(path,cwd)).as_posix(),'object':Path(os.path.relpath(obj,cwd)).as_posix()}
                argv=[part.format(**values) for part in command]
                run=subprocess.run(argv,cwd=cwd,capture_output=True,text=True,encoding='utf-8',timeout=90)
                path.with_suffix('.log').write_text(run.stdout+run.stderr,encoding='utf-8')
                run.check_returncode()
                if any(word in (run.stdout+run.stderr).lower() for word in ('warning:', 'error:')):raise ValueError('Compiler diagnostics require review.')
                pair.append(dict(phase=phase,sourceHash=sha(path.read_bytes()),sourceLFHash=sha(source.encode()),**object_info(obj)))
            targets=lambda p:sorted((r['section'],r['type'],r['symbol'],r['symbolValue']) for r in p['relocations'])
            if targets(pair[0])!=targets(pair[1]):raise ValueError('External relocation inventory differs.')
            results.append(dict(context=context,rule=label,headerHash=sha(header.encode()),pair=pair,
                                sameCode=pair[0]['textHash']==pair[1]['textHash'],sameExternalRelocationInventory=True))
    report=dict(syntheticSourcesOnly=True,retailComparison=False,semanticApproval=False,compilerRuns=len(results)*2,cases=results)
    (out/'evidence.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(cases=len(results),compilerRuns=len(results)*2,output=str(out))))


if __name__=='__main__':main()
