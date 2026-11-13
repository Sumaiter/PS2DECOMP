"""Recover complete constant-only EE constructors, including two MMI pack ops.

Known bits are tracked across 32/64-bit scalar writes and 128-bit registers.
Unknown input bits, calls, branches, loads and unsupported operations reject the
whole pattern. This creates a draft, not a verified original type or byte match.
"""
import math
import struct

MASK64=(1<<64)-1
MASK128=(1<<128)-1

def derive(name,raw,start):
    if not raw or len(raw)%4 or len(raw)>2048:return None
    words=struct.unpack('<'+'I'*(len(raw)//4),raw)
    if len(words)<3 or words[-2]!=0x03e00008:return None
    registers=[(0,0) for _ in range(32)];registers[0]=(0,MASK128)
    floats=[None]*32;stores=[];used_mmi=False
    def signed32(value):return (value&0x7fffffff)-(value&0x80000000)
    def known(reg,bits):
        value,mask=registers[reg]
        return value&((1<<bits)-1) if mask&((1<<bits)-1)==(1<<bits)-1 else None
    def low64(reg,value):
        if reg in (4,29,31):raise ValueError('Input/frame/return register overwritten')
        if reg:
            old,mask=registers[reg]
            registers[reg]=((old&~MASK64)|(value&MASK64),(mask&~MASK64)|MASK64)
    try:
        for index,w in enumerate(words):
            if not w:continue
            if index==len(words)-2:continue
            op,rs,rt,rd,shift,fn=w>>26,(w>>21)&31,(w>>16)&31,(w>>11)&31,(w>>6)&31,w&63
            immediate=(w&0x7fff)-(w&0x8000)
            if op==15 and rs==0:
                low64(rt,signed32((w&0xffff)<<16))
            elif op==9:
                value=known(rs,32)
                if value is None:return None
                low64(rt,signed32((value+immediate)&0xffffffff))
            elif op==13:
                value=known(rs,64)
                if value is None:return None
                low64(rt,value|(w&0xffff))
            elif op==0 and fn in (0,56,60):
                if rs:return None
                amount=shift+(32 if fn==60 else 0)
                bits=32 if fn==0 else 64
                value=known(rt,bits-amount)
                if value is None:return None
                result=(value<<amount)&((1<<bits)-1)
                low64(rd,signed32(result) if bits==32 else result)
            elif op==28 and (fn,shift) in ((8,18),(9,14)):
                first,second=known(rs,64),known(rt,64)
                if first is None or second is None or rd in (4,29,31):return None
                if (fn,shift)==(8,18):
                    value=(second&0xffffffff)|((first&0xffffffff)<<32)|((second>>32)<<64)|((first>>32)<<96)
                else:value=second|(first<<64)
                if rd:registers[rd]=(value,MASK128)
                used_mmi=True
            elif op==17 and rs==4:
                if w&0x7ff:return None
                value=known(rt,32)
                if value is None:return None
                floats[rd]=value
            elif op in (31,43,57,63) and rs==4:
                width={31:16,43:4,57:4,63:8}[op]
                if immediate<0 or immediate%width or immediate+width>0x100000:return None
                value=floats[rt] if op==57 else known(rt,width*8)
                if value is None:return None
                if op==57:
                    value_float=struct.unpack('<f',struct.pack('<I',value))[0]
                    if not math.isfinite(value_float):return None
                    expression=repr(value_float)+'f';kind='f32'
                elif width==16:
                    expression=f'(((u128)0x{value>>64:016X}UL << 64) | (u128)0x{value&MASK64:016X}UL)';kind='u128'
                else:expression=f'0x{value:0{width*2}X}'+('UL' if width==8 else 'U');kind='u64' if width==8 else 'u32'
                stores.append(dict(offset=immediate,bytes=width,bits=f'{value:0{width*2}X}',kind=kind,expression=expression))
            else:return None
        if not stores or not used_mmi:return None
    except ValueError:return None
    lines=['#include "common.h"','typedef unsigned long long u128;',
           'typedef char Rac4ConstantTypeWidths[(sizeof(u128)==16 && sizeof(u64)==8 && sizeof(f32)==4)?1:-1];',
           '/* Constant-only reconstruction from all original instructions.',
           ' * Object layout/alignment and original nominal types require review. */',
           'void '+name+'(u8 *object)','{']
    for store in stores:
        lines.append(f'    *({store["kind"]} *)(object + 0x{store["offset"]:X}) = {store["expression"]};')
    lines.append('}')
    return dict(source='\n'.join(lines)+'\n',rule='constant-mmi-constructor',facts=dict(entry=start,coversWholeFunction=True,requiresSN128=True,stores=stores))
