"""Read-only source/object/reference evidence; never a semantic approval."""
from pathlib import Path
import hashlib,io,json,re,struct
import rough
import source_fidelity
from search_rules import code_only
from elftools.elf.relocation import RelocationSection

MEMORY={32:('read',1),33:('read',2),34:('read-merge',4),35:('read',4),
        36:('read',1),37:('read',2),38:('read-merge',4),39:('read',4),
        40:('write',1),41:('write',2),42:('write-merge',4),43:('write',4),
        44:('write-merge',8),45:('write-merge',8),46:('write-merge',4),
        26:('read-merge',8),27:('read-merge',8),30:('read',16),31:('write',16),
        49:('read-float',4),53:('read-float',8),54:('read-vector',16),
        55:('read',8),57:('write-float',4),61:('write-float',8),
        62:('write-vector',16),63:('write',8)}
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()


def memory_accesses(raw,start):
    """Encoded access widths/offsets, without guessing pointers or data ownership."""
    rows=[]
    for off,(word,) in zip(range(0,len(raw),4),struct.iter_unpack('<I',raw)):
        op=word>>26
        if op not in MEMORY:continue
        action,width=MEMORY[op]
        rows.append(dict(address=start+off,offset=off,action=action,bytes=width,
                         baseRegister=(word>>21)&31,valueRegister=(word>>16)&31,
                         displacement=(word&0x7fff)-(word&0x8000),word=f'{word:08X}'))
    return rows


def placement_diagnostics(report):
    """Explain independent placement failures, without assigning symbol aliases.

    Relocations may only be compared at matching instruction offsets. A known
    different catalogue name is an identity question, not proof of equivalence.
    """
    findings=[]
    for place in report['placements']:
        context=dict(program=place['program'],address=place['address'])
        if not place['instructionMatch']:
            findings.append(dict(context,kind='instruction-sequence',
                detail='Instruktionsfolge abweichend; Relokationen nicht positionsgleich zertifiziert.'))
            continue
        relocations=place.get('relocations') or {}
        for relocation in relocations.get('checked',[]):
            if relocation.get('ok'):
                continue
            entry=dict(context,offset=relocation['offset'],symbol=relocation['symbol'],
                       expected=relocation.get('expected'),actual=relocation.get('actual'))
            if relocation['type']==4:
                call=next((c for c in place['directCalls']
                           if c['instruction']==place['address']+relocation['offset']),None)
                actual_name=call.get('catalogueName') if call else None
                entry.update(kind='call-target-binding',actualCatalogueName=actual_name,
                    detail='Aufrufziel weicht ab; Symbolidentität über alle Platzierungen manuell klären. Keine automatische Umleitung.')
            else:
                entry.update(kind='data-target-binding',
                    detail='Tatsächliches Datenziel weicht ab; Datenbesitz und Platzierungsbindung klären.')
            findings.append(entry)
        for unresolved in relocations.get('unresolved',[]):
            findings.append(dict(context,kind='unresolved-relocation',evidence=unresolved,
                detail='Relokation nicht vollständig aufgelöst; kein technischer Abschluss.'))
    return findings


def inspect(engine,name,candidate=None):
    candidate=candidate or {}
    source_path=Path(candidate['snapshot']) if candidate.get('snapshot') else None
    source_ok=bool(source_path and source_path.is_file() and candidate.get('sourceHash')==sha(source_path))
    source=source_path.read_text(encoding='utf-8') if source_ok else ''
    text=code_only(source)
    signature=re.search(r'(?m)^([\w \t*]+\b'+re.escape(name)+r'\s*\([^;{}]*\))\s*\{',text)
    markers=sorted(set(re.findall(r'\b(?:M2C_\w+|saved_reg_\w+|UNK_\w+)\b',text)))
    declared,conflicts=rough.context(engine.contracts,[name])
    snapshot_conflicts=[]
    if signature and engine.contracts.get(name):
        _,snapshot_conflicts=rough.context({name:[*engine.contracts[name],
            dict(declaration=signature[1]+';')]},[name])
    report=dict(name=name,source=str(source_path) if source_path else None,sourceHash=candidate.get('sourceHash'),
        sourceVerified=source_ok,objectVerified=False,objectHash=None,technicalPassed=False,
        semanticApproval=False,manualReviewRequired=True,
        types=dict(snapshotSignature=signature[1] if signature else None,unresolvedMarkers=markers,
                   recordedContracts=engine.contracts.get(name,[]),usablePrimitiveContracts=declared,contractWarnings=conflicts,
                   snapshotContractWarnings=snapshot_conflicts,
                   verdict='Snapshot and recorded declarations are evidence, not proof of original types.'),
        ownership=dict(definitions=[],allocatedSections=[],verdict='Runtime ownership and initial values require manual evidence.'),
        sourceFidelity=source_fidelity.inspect(source, engine.root),
        compiledMemoryAccesses=[],objectRelocations=[],placements=[],problems=[])
    if source_path and not source_ok:report['problems'].append('Source hash missing or changed; source/object certification refused.')
    obj=source_path.with_suffix('.o') if source_path else None
    code=masks=start=elf=None
    if source_ok and obj.exists():
        digest=sha(obj);report['objectHash']=digest
        report['objectVerified']=bool(candidate.get('objectHash') and digest==candidate['objectHash'])
        if not report['objectVerified']:report['problems'].append('Object hash missing or changed; compiler/source binding remains unverified.')
        if candidate.get('objectHash') and digest!=candidate['objectHash']:
            obj=None
        else:
            try:
                elf=engine.ELFFile(io.BytesIO(obj.read_bytes()))
                symbols=elf.get_section_by_name('.symtab')
                definitions=[dict(name=s.name,type=s['st_info']['type'],size=s['st_size']) for s in symbols.iter_symbols()
                    if isinstance(s['st_shndx'],int) and (s['st_info']['type']=='STT_OBJECT' or s['st_info']['bind']=='STB_GLOBAL')]
                sections=[dict(name=s.name,size=s['sh_size'],kind=s['sh_type'],sha256=hashlib.sha256(s.data()).hexdigest())
                    for s in elf.iter_sections() if s['sh_flags']&2 and s['sh_size']]
                report['ownership'].update(definitions=definitions,allocatedSections=sections)
                high={}
                for section in elf.iter_sections():
                    if not isinstance(section,RelocationSection):continue
                    st=elf.get_section(section['sh_link']);target=elf.get_section(section['sh_info'])
                    for rel in section.iter_relocations():
                        symbol=st.get_symbol(rel['r_info_sym']).name
                        off,kind=rel['r_offset'],rel['r_info_type']
                        address=engine.symbol_address(symbol,engine.place(name)[0])
                        word=struct.unpack_from('<I',target.data(),off)[0]
                        entry=dict(section=target.name,offset=off,type=kind,symbol=symbol,symbolBase=address)
                        key=(target.name,symbol)
                        if kind==5:high.setdefault(key,[]).append((off,word&0xffff))
                        elif kind==6 and key in high:
                            pairs=high[key]
                            entry['pairedHighOffsets']=[p[0] for p in pairs]
                            if word>>26 not in (8,9,24,25,32,33,35,36,37,39,40,41,43,49,55,57,63):
                                entry['unresolved']='LO16 opcode needs separate addend interpretation'
                            else:
                                entry['resolvedExpectedTargets']=[None if address is None else
                                    (address+(hi<<16)+(word&0x7fff)-(word&0x8000))&0xffffffff for _,hi in pairs]
                            high[key]=pairs[-1:]
                        elif kind==4:
                            addend=(word&0x3ffffff)<<2
                            if addend&0x8000000:addend-=0x10000000
                            entry['resolvedExpectedTarget']=None if address is None else (address+addend)&0xffffffff
                        elif kind==7:
                            entry['resolvedExpectedTarget']=None if address is None else (address+(word&0x7fff)-(word&0x8000))&0xffffffff
                        report['objectRelocations'].append(entry)
                compiled=engine.try_func.compiled(obj,name)
                if compiled:
                    code,masks,start=compiled
                    report['compiledMemoryAccesses']=memory_accesses(code,0)
            except Exception as exc:
                report['problems'].append('Object inspection failed: '+str(exc));code=None
    else:
        report['problems'].append('No verified compiled snapshot available.')
    references=set();primary=None
    for place in engine.all_places(name):
        if place[0] not in references:engine.assert_reference(place[0]);references.add(place[0])
        raw=engine.check.disc_bytes(place)
        same=bool(code is not None and len(code)==len(raw) and
                  engine.retail.fields(code,masks,start)==engine.retail.fields(raw,masks,start))
        # Aligned relocation checks only make sense after an instruction match.
        targets=engine.relocations(obj,code,start,raw,place) if same else None
        row=dict(program=place[0],section=place[1],address=place[2],size=place[3],
                 referenceHash=hashlib.sha256(raw).hexdigest(),instructionMatch=same,
                 relocations=targets,targetsPassed=bool(targets and targets['passed']),
                 memoryAccesses=memory_accesses(raw,place[2]),directCalls=[],indirectCalls=[])
        for off,(word,) in zip(range(0,len(raw),4),struct.iter_unpack('<I',raw)):
            if word>>26==3:
                address=((place[2]+off+4)&0xf0000000)|((word&0x3ffffff)<<2)
                row['directCalls'].append(dict(instruction=place[2]+off,target=address,
                    catalogueName=getattr(engine,'_symbols',{}).get(place[0],{}).get(address)))
            elif word>>26==0 and word&63==9:
                row['indirectCalls'].append(dict(instruction=place[2]+off,targetRegister=(word>>21)&31,
                                                actualRuntimeTarget='unresolved; manual callback/vtable evidence required'))
        report['placements'].append(row)
        if primary is None:primary=dict(snapshot=str(source_path),byteMatch=same,relocations=targets,
            sourceHash=candidate.get('sourceHash'),objectHash=candidate.get('objectHash'))
    if code is not None and primary and primary['byteMatch']:
        report['strict']=engine.strict(name,primary)
        report['technicalPassed']=bool(report['strict']['passed'] and report['sourceVerified'] and report['objectVerified'])
    else:report['strict']=dict(passed=False,problems=['No complete primary instruction match.'])
    report['placementDiagnostics']=placement_diagnostics(report)
    report['manualChecks']=['Original parameter/return types, incoming ABI slots and callers',
        'Pointer validity, alignment, aliasing and signed/unsigned arithmetic',
        'Actual runtime initial values and ownership of every global/table/buffer',
        'True function boundaries, indirect callbacks and the meaning of each access']
    return report


def markdown(report):
    rows=['# PS2DECOMP Prüfbericht: '+report['name'],'',
          '**Technische Prüfung: '+('bestanden' if report['technicalPassed'] else 'offen / abweichend')+'**',
          '**Fachliche Freigabe: offen — manuelle Review erforderlich.**','',
          'Der Suchwert ist kein Korrektheitsprozent. Dieser Bericht verändert keine Quellen, Claims oder Match-Zähler.','',
          '## Typen und ABI','', 'Signatur im Snapshot: `'+str(report['types']['snapshotSignature'])+'`',
          'Offene Platzhalter: '+(', '.join(report['types']['unresolvedMarkers']) or 'keine erkannt'),'']
    rows+=['```json',json.dumps(report['types'],indent=2,ensure_ascii=False),'```','']
    rows+=['## Plain-C-Quellprüfung','',
           'Nur lesende Diagnose. Pointerliterale werden nicht in Symbole umgewandelt; fehlende Hinweise sind keine fachliche Freigabe.','',
           '```json',json.dumps(report.get('sourceFidelity',{}),indent=2,ensure_ascii=False),'```','']
    for warning in report['types']['contractWarnings']+report['types']['snapshotContractWarnings']+report['problems']:rows.append('- '+warning)
    rows+=['','## Datenbesitz und Initialwerte','',
           'Die folgenden Objektdefinitionen und Segmente sind compilerseitige Belege. Laufzeitwerte und ursprünglicher Datenbesitz werden nicht automatisch bestätigt.','',
           '```json',json.dumps(report['ownership'],indent=2,ensure_ascii=False),'```','',
           '## Speicherzugriffe im Compilerobjekt','',
           'Offsets sind relativ zur Funktion; Registerbreiten sind keine automatisch bestätigten C-Typen.','',
           '```json',json.dumps(report['compiledMemoryAccesses'],indent=2,ensure_ascii=False),'```','',
           '### Relokationen des Objekts','',
           'Erwartete Symbolziele sind separat aufgeführt. Ohne passende Instruktionsfolge sind sie noch nicht gegen dieselben Originalstellen zertifiziert.','',
           '```json',json.dumps(report['objectRelocations'],indent=2,ensure_ascii=False),'```','',
           '## Alle katalogisierten Platzierungen','',
           '| Programm | Adresse | Bytes | Instruktionen | Relokationsziele |','|---|---:|---:|---|---|']
    for p in report['placements']:
        rows.append(f"| {p['program']} | 0x{p['address']:08X} | {p['size']} | {'passend' if p['instructionMatch'] else 'abweichend / ungeprüft'} | {'bestätigt' if p['targetsPassed'] else 'offen / abweichend'} |")
    rows+=['','### Gründe für offene Platzierungen','',
           'Diese Diagnose unterscheidet Codeabweichungen von ungeklärten Aufruf- und Datenbindungen. Andere Katalognamen werden nicht als gleichwertig angenommen.','',
           '```json',json.dumps(report.get('placementDiagnostics',[]),indent=2,ensure_ascii=False),'```','']
    for p in report['placements']:
        rows+=['',f"### {p['program']} · Speicherzugriffe und Relokationsbelege",'',
               'Die Breite stammt aus dem Opcode. Ein Registerzugriff beweist keinen C-Zeigertyp. Merge-Zugriffe dürfen nicht als vollständige unabhängige Stores interpretiert werden.','',
               '| Instruktionsadresse | Zugriff | Breite | Basisregister | Versatz |','|---|---|---:|---|---:|']
        for access in p['memoryAccesses']:
            rows.append(f"| 0x{access['address']:08X} | {access['action']} | {access['bytes']} B | r{access['baseRegister']} | {access['displacement']} |")
        rows+=['','```json',json.dumps(dict(relocations=p['relocations'],directCalls=p['directCalls'],indirectCalls=p['indirectCalls']),indent=2,ensure_ascii=False),'```']
    rows+=['','## Manuell offen','']+['- '+s for s in report['manualChecks']]
    from presentation_language import markdown as localize_markdown
    return localize_markdown('\n'.join(rows)+'\n')


def write_report(engine,name,candidate):
    result=inspect(engine,name,candidate)
    folder=rough.directory(engine.root,name,'review-checks')
    (folder/'checks.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    path=folder/'Pruefbericht.md';path.write_text(markdown(result),encoding='utf-8')
    return dict(path=str(path),json=str(folder/'checks.json'),technicalPassed=result['technicalPassed'],manualReviewRequired=True)
