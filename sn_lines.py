"""Narrow, fail-closed reader for observed SN2.95 ELF32 little-MIPS ECOFF stabs.

No compiler or production changes. Requires pyelftools. Offsets are within the
object's .text section, not retail addresses. Optimized debug attribution is
not a claim that every instruction originated solely from the displayed line.
"""
from pathlib import Path
from collections import defaultdict
import argparse, hashlib, json, re, struct
from elftools.elf.elffile import ELFFile

def sha(data): return hashlib.sha256(data).hexdigest()
def require(condition, message):
    if not condition: raise ValueError(message)

def fingerprint(path):
    raw=Path(path).read_bytes()
    with Path(path).open('rb') as stream:
        elf=ELFFile(stream)
        require(elf.elfclass==32 and elf.little_endian and elf['e_machine']=='EM_MIPS' and elf['e_type']=='ET_REL','Expected ELF32 little-endian MIPS relocatable object')
        allocated=[];relocations=[];functions=[]
        for sec in elf.iter_sections():
            if sec['sh_flags']&2:
                allocated.append({'name':sec.name,'type':sec['sh_type'],'flags':sec['sh_flags'],'alignment':sec['sh_addralign'],'size':sec['sh_size'],'address':sec['sh_addr'],'sha256':sha(sec.data())})
            if sec['sh_type'] in ('SHT_REL','SHT_RELA'):
                target=elf.get_section(sec['sh_info'])
                require(target is not None,'Relocation references invalid target section')
                if not target['sh_flags']&2:continue
                syms=elf.get_section(sec['sh_link'])
                require(syms is not None and syms['sh_type'] in ('SHT_SYMTAB','SHT_DYNSYM'),'Invalid relocation symbol table')
                for relocation in sec.iter_relocations():
                    require(relocation['r_info_type'] in (2,4,5,6,7,9,10,11),'Unsupported relocation width/type in narrow MIPS32 reader')
                    require(0<=relocation['r_offset'] and relocation['r_offset']%4==0 and relocation['r_offset']+4<=target['sh_size'],'Relocation outside/alignment of allocated section')
                    symbol=syms.get_symbol(relocation['r_info_sym']);idx=symbol['st_shndx']
                    symbol_section=elf.get_section(idx).name if isinstance(idx,int) else idx
                    relocations.append({'section':target.name,'offset':relocation['r_offset'],'type':relocation['r_info_type'],'symbol':symbol.name,'symbol_section':symbol_section,'symbol_type':symbol['st_info']['type'],'symbol_bind':symbol['st_info']['bind'],'symbol_value':symbol['st_value'],'symbol_size':symbol['st_size'],'addend':relocation.entry.get('r_addend')})
        symtab=elf.get_section_by_name('.symtab')
        require(symtab is not None,'No ELF symbol table')
        for symbol in symtab.iter_symbols():
            if symbol['st_info']['type']=='STT_FUNC' and isinstance(symbol['st_shndx'],int):
                section=elf.get_section(symbol['st_shndx'])
                functions.append({'name':symbol.name,'section':section.name,'start':symbol['st_value'],'size':symbol['st_size']})
    return {'object_sha256':sha(raw),'allocated_sections':sorted(allocated,key=lambda x:x['name']),'allocated_relocations':sorted(relocations,key=lambda x:(x['section'],x['offset'],x['type'],x['symbol'])),'functions':sorted(functions,key=lambda x:(x['section'],x['start'],x['name']))}

def line_map(canonical, debug, source):
    canonical,debug,source=map(Path,(canonical,debug,source))
    a,b=fingerprint(canonical),fingerprint(debug)
    require(a['allocated_sections']==b['allocated_sections'],'Debug object changes allocated bytes/layout; mapping refused')
    require(a['allocated_relocations']==b['allocated_relocations'],'Debug object changes actual allocated-section relocations; mapping refused')
    require(a['functions']==b['functions'],'Debug object changes function ranges; mapping refused')
    source_lines=source.read_text(encoding='utf-8-sig').splitlines()
    with debug.open('rb') as stream:
        elf=ELFFile(stream);sec=elf.get_section_by_name('.mdebug');text=elf.get_section_by_name('.text')
        require(sec is not None and text is not None,'No .mdebug/.text')
        for relsec in elf.iter_sections():
            if relsec['sh_type'] in ('SHT_REL','SHT_RELA') and relsec['sh_info']==next(i for i,s in enumerate(elf.iter_sections()) if s.name=='.mdebug'):
                require(relsec.num_relocations()==0,'Relocated .mdebug is not supported by this narrow reader')
        data=sec.data();base=sec['sh_offset']
        require(len(data)>=96,'Truncated ECOFF HDRR')
        h=struct.unpack_from('<HH23I',data)
        require(h[0]==0x7009 and h[1]==0x020B,'Unsupported ECOFF magic/version (observed 7009/020B)')
        require(h[19]==1,'Only observed single-FDR compilation unit is supported')
        def block(absolute,size):
            offset=absolute-base
            require(0<=offset<=len(data) and 0<=size<=len(data)-offset,'ECOFF table out of .mdebug bounds')
            return data[offset:offset+size]
        syms=block(h[10],h[9]*12);strings=block(h[16],h[15]);fdr=block(h[20],72)
        fd=struct.unpack_from('<10I',fdr)
        require(fd[2]==0 and fd[4]==0 and fd[5]==h[9] and fd[3]<=len(strings),'Unsupported FDR string/symbol subranges')
        def string(index):
            require(0<=index<fd[3],'String index outside FDR string table')
            end=strings.find(b'\0',index,fd[3]);require(end>=0,'Unterminated ECOFF string')
            return strings[index:end].decode('utf-8',errors='strict')
        current_file=string(fd[1]);events=[];excluded=[];has_stabs=False
        for i in range(h[9]):
            iss,value,bits=struct.unpack_from('<III',syms,i*12)
            st=bits&63;sc=(bits>>6)&31;index=bits>>12;name=string(iss)
            if name=='@stabs' and index==0x8F300:has_stabs=True
            if index in (0x8F364,0x8F384) and name and not name.endswith(('/','\\')):current_file=name
            if not re.fullmatch(r'\$LM[0-9]+',name):continue
            require(st==5 and sc==1,'SN line marker is not stLabel/scText')
            require(0<=value<=text['sh_size'] and value%4==0,'Line marker outside/alignment of .text')
            # SN gas translates .stabn 68 into label/scText, with source line
            # in the 20-bit index instead of retaining a normal N_SLINE stab.
            require(index<0x8F300,'Unexpected encoded stab type in line marker')
            event={'offset':value,'line':index,'source':current_file,'symbol':name,'symbol_record':i}
            # Match full snapshot path, never a matching basename/header.
            # Compiler paths are relative to N_SO's compilation directory,
            # not the object's directory. Resolve after reading that record.
            event['source_matches_snapshot']=False
            events.append(event)
        require(has_stabs,'No observed SN stabs marker')
        # N_SO directory is the authoritative compilation working directory.
        dirs=[]
        for i in range(h[9]):
            iss,value,bits=struct.unpack_from('<III',syms,i*12)
            if bits>>12==0x8F364:
                textname=string(iss)
                if textname.endswith(('/','\\')):dirs.append(Path(textname))
        require(len(dirs)==1 and dirs[0].is_absolute(),'Unsupported/missing compilation directory')
        for event in events:
            resolved=Path(event['source'])
            if not resolved.is_absolute():resolved=dirs[0]/resolved
            event['source_matches_snapshot']=resolved.resolve()==source.resolve()
            if event['line']==0 or not event['source_matches_snapshot']:excluded.append(event)
            else:require(event['line']<=len(source_lines),'Source line beyond snapshot bounds')
        ranges=[]
        for fn in b['functions']:
            if fn['section']!='.text':continue
            require(0<fn['size'] and 0<=fn['start'] and fn['start']+fn['size']<=text['sh_size'],'Invalid function bounds')
            own=[e for e in events if fn['start']<=e['offset']<fn['start']+fn['size']]
            byoffset=defaultdict(list)
            for event in own:byoffset[event['offset']].append(event)
            offsets=sorted(byoffset)
            for n,start in enumerate(offsets):
                end=offsets[n+1] if n+1<len(offsets) else fn['start']+fn['size']
                # Every label is a range boundary, including line0/header
                # labels. Do not carry a prior user line across those labels.
                row=byoffset[start][-1]
                if row['line']==0 or not row['source_matches_snapshot']:continue
                ranges.append({'function':fn['name'],'start':start,'end_exclusive':end,'line':row['line'],'source':str(source.resolve()),'labels_at_offset':byoffset[start],'attribution':'last compiler line label at address; valid until next compiler label or function end'})
    return {'status':'verified_identical_objects','format':'ELF32 little-MIPS .mdebug ECOFF-stabs 7009/020B; single FDR','canonical':str(canonical.resolve()),'debug':str(debug.resolve()),'source_snapshot':str(source.resolve()),'source_sha256':sha(source.read_bytes()),'parser_sha256':sha(Path(__file__).read_bytes()),'canonical_fingerprint':a,'debug_fingerprint':b,'compiler_line_events':events,'excluded_zero_or_header_events':excluded,'ranges':ranges,'limitations':['Compiler debug-line attribution under -O2; no columns, causal one-to-one C-expression mapping, or universal code-generation equivalence is claimed.','Only observed single-FDR SN format supported; other endianness, table layouts, relocated debug data and unknown markers fail closed.','Caller must compile both objects from the same immutable source snapshot with identical flags except -g; identity checks run on every pair.']}

if __name__=='__main__':
    cli=argparse.ArgumentParser();cli.add_argument('canonical');cli.add_argument('debug');cli.add_argument('source');cli.add_argument('--output',required=True);args=cli.parse_args()
    result=line_map(args.canonical,args.debug,args.source)
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'ranges':len(result['ranges']),'source_sha256':result['source_sha256']}))
