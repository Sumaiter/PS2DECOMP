"""Tiny synthetic ELF writer. These instructions are test data, not game code."""
import struct


def elf(words, *, name='func_00100000', address=0x100000, executable=False, relocations=(), external='global_value'):
    text = struct.pack('<'+'I'*len(words), *words)
    strings = b'\0'+name.encode()+b'\0'+external.encode()+b'\0'
    symbols = b'\0'*16 + struct.pack('<IIIBBH',1,0,len(text),0x12,0,1)
    if relocations:
        symbols += struct.pack('<IIIBBH',len(name)+2,0,0,0x10,0,0)
    sections = [('',0,0,0,b'',0,0,0),('.text',1,6,address if executable else 0,text,0,0,0),
                ('.strtab',3,0,0,strings,0,0,0),('.symtab',2,0,0,symbols,2,1,16)]
    if relocations:
        body=b''.join(struct.pack('<II',offset,(2<<8)|kind) for offset,kind in relocations)
        sections.append(('.rel.text',9,0,0,body,3,1,8))
    shstr=b'\0'; offsets={}
    for s in sections[1:]+[('.shstrtab',)]:
        offsets[s[0]]=len(shstr);shstr+=s[0].encode()+b'\0'
    sections.append(('.shstrtab',3,0,0,shstr,0,0,0))
    blob=bytearray(b'\0'*52);headers=[]
    for label,kind,flags,addr,data,link,info,entsize in sections:
        while len(blob)%4:blob.append(0)
        off=len(blob);blob.extend(data)
        headers.append(struct.pack('<IIIIIIIIII',offsets.get(label,0),kind,flags,addr,off if kind else 0,len(data),link,info,4 if kind not in (0,3) else 1,entsize))
    while len(blob)%4:blob.append(0)
    shoff=len(blob);blob.extend(b''.join(headers))
    ident=b'\x7fELF\x01\x01\x01'+b'\0'*9
    blob[:52]=struct.pack('<16sHHIIIIIHHHHHH',ident,2 if executable else 1,8,1,address if executable else 0,0,shoff,0,52,0,0,40,len(sections),len(sections)-1)
    return bytes(blob)
