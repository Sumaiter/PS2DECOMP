from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.elf_fixture import elf
from project_engine import Engine
from project_claims import ClaimAPI
import project_coordination
from auto_jobs import Manager, search_job, read


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.name='func_00100000'
        for directory in ('include','config','tools','baserom'):(self.root/directory).mkdir()
        shutil.copy2(Path(__file__).parent/'fixtures/project/include/common.h',self.root/'include/common.h')
        self.words=[0x03e00008,0x2402002a]
        self.write('baserom/main.elf',elf(self.words,executable=True))
        self.write('tools/template.o',elf(self.words))
        script=r'''from pathlib import Path
import re,sys,struct
source,output=sys.argv[1:3]
text=Path(source).read_text()
match=re.search(r'return\s+(\d+)\s*;',text)
if not match: raise SystemExit('synthetic compiler rejects unknown source')
blob=bytearray(Path(__file__).with_name('template.o').read_bytes())
struct.pack_into('<I',blob,56,0x24020000|int(match[1]))
Path(output).write_bytes(blob)
'''
        self.write('tools/compile.py',script.encode())
        self.inventory=[dict(name=self.name,placements=[dict(program='main',section='.text',address='0x00100000',size=8)])]
        self.settings=dict(schemaVersion=1,name='Synthetic Test Game',version='test-pal',adapter='generic',mainProgram='main',
            programs={'main':dict(elf='baserom/main.elf',sha256=self.sha('baserom/main.elf'),symbols={},
                eeSections=['.text'],eeSectionsEvidence='Synthetic EE-only executable section.')},
            functions='config/functions.json',compileCommand=['{python}','tools/compile.py','{source}','{object}','{name}','{program}'],
            inputs={p:self.sha(p) for p in ('tools/compile.py','tools/template.o','include/common.h')},coordination={'mode':'local'})
        self.save()

    def write(self,p,data):(self.root/p).write_bytes(data)
    def sha(self,p):return hashlib.sha256((self.root/p).read_bytes()).hexdigest()
    def save(self):
        (self.root/'config/functions.json').write_text(json.dumps(self.inventory))
        (self.root/'ps2decomp.json').write_text(json.dumps(self.settings))
    def source(self,value=42):return '#include "common.h"\nint '+self.name+'(void){return '+str(value)+';}\n'

    def test_end_to_end_compiler_protocol_and_changed_body_negative(self):
        engine=Engine(self.root)
        result=engine.dispatch(dict(op='compare',name=self.name,source=self.source()))
        self.assertTrue(result['byteMatch'],result['log']);self.assertTrue(engine.strict(self.name,result)['passed'])
        import review_checks
        report=review_checks.inspect(engine,self.name,result)
        self.assertTrue(report['technicalPassed'],report['strict'])
        changed=engine.dispatch(dict(op='compare',name=self.name,source=self.source(43)))
        self.assertFalse(changed['byteMatch']);self.assertFalse(engine.strict(self.name,changed)['passed'])
        self.assertFalse((self.root/'build/studio'/self.name/'draft.c').exists())

    def test_queue_preserves_claim_until_explicit_handoff_and_never_overwrites_draft(self):
        manager=Manager(self.root)
        session=manager.create([self.name],dict(workers=1,maxAttempts=2,seconds=15),seeds={self.name:self.source()})
        api=ClaimAPI(self.root);self.assertEqual(api.holder(self.name),session['owner'])
        search_job(manager,session['id'],self.name)
        job=read(manager.folder(session['id'])/'jobs'/self.name/'job.json')
        self.assertEqual(job['status'],'review_ready',job)
        self.assertTrue(job['best']['technicalMatch']);self.assertEqual(api.holder(self.name),session['owner'])
        with self.assertRaises(ValueError):Engine(self.root).dispatch(dict(op='compare',name=self.name,source=self.source()))
        manager.handoff(session['id'],self.name)
        self.assertIsNone(api.holder(self.name))
        draft=self.root/'build/studio'/self.name/'draft.c';draft.parent.mkdir(parents=True,exist_ok=True);draft.write_text('user draft')
        Engine(self.root).create(self.name,'replacement')
        self.assertEqual(draft.read_text(),'user draft')

    def test_changed_reference_or_compiler_hash_fails_closed(self):
        engine=Engine(self.root)
        self.write('baserom/main.elf',elf([0x03e00008,0x2402002b],executable=True))
        with self.assertRaises(ValueError):engine.disc_bytes(engine.place(self.name))
        self.write('tools/compile.py',b'raise SystemExit(0)')
        result=engine.compare(self.name,self.source())
        self.assertFalse(result['byteMatch']);self.assertIn('SHA-256 differs',result['log'])

    def test_atomic_competitors_and_foreign_release(self):
        api=ClaimAPI(self.root)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda i:api.claim('owner-'+str(i),self.name),range(8)))
        self.assertEqual(sum(results),1)
        holder=api.holder(self.name);self.assertFalse(api.release('foreign',self.name));self.assertEqual(api.holder(self.name),holder)
        self.assertTrue(api.release(holder,self.name))

    def test_coordination_wrong_version_incomplete_and_overlapping_scope_rejected(self):
        engine=Engine(self.root)
        engine.config['coordination']=dict(mode='command',command=[sys.executable,'-c',
            'import json; print(json.dumps({"complete":True,"version":"wrong","blocked":[],"allowed":[]}))'])
        with self.assertRaises(ValueError):project_coordination.assert_free(engine,[self.name])
        engine.config['coordination']['command'][-1]='import json; print(json.dumps({"complete":False,"version":"test-pal","blocked":[],"allowed":[]}))'
        with self.assertRaises(ValueError):project_coordination.assert_free(engine,[self.name])

    def test_both_languages_do_not_change_backend_evidence(self):
        request=json.dumps(dict(id=1,op='compare',name=self.name,source=self.source()))+'\n'
        import os
        for lang in ('en','de'):
            run=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'backend.py'),'--repo',str(self.root)],
                input=request,capture_output=True,text=True,env={**os.environ,'PS2DECOMP_LANGUAGE':lang},timeout=30)
            self.assertEqual(run.returncode,0,run.stderr)
            response=json.loads(run.stdout.splitlines()[-1]);self.assertTrue(response['result']['byteMatch'],response)

    def relocation_case(self, native, compiled, rels):
        self.write('baserom/main.elf',elf(native,executable=True));self.settings['programs']['main']['sha256']=self.sha('baserom/main.elf')
        self.settings['programs']['main']['symbols']={'global_value':'0x00308004'}
        self.inventory[0]['placements'][0]['size']=len(native)*4;self.save()
        obj=self.root/'tools/relocation.o';obj.write_bytes(elf(compiled,relocations=rels))
        engine=Engine(self.root);code,masks,start=engine.compiled(obj,self.name)
        return engine,obj,code,start,engine.disc_bytes(engine.place(self.name))

    def test_hi_lo_signed_addend_targets_and_changed_symbol_negative(self):
        engine,obj,code,start,want=self.relocation_case([0x3c020031,0x8c428004,0x03e00008,0],
            [0x3c020000,0x8c420000,0x03e00008,0],[(0,5),(4,6)])
        self.assertTrue(engine.relocations(obj,code,start,want,engine.place(self.name))['passed'])
        engine.config['programs']['main']['symbols']['global_value']='0x00308008'
        refs=engine.relocations(obj,code,start,want,engine.place(self.name))
        self.assertFalse(refs['passed']);self.assertTrue(any(not r['ok'] for r in refs['checked']))

    def test_unsupported_and_unpaired_relocations_fail_closed(self):
        for rels in ([(0,5)],[(0,99)],[(100,4)]):
            engine,obj,code,start,want=self.relocation_case([0x3c020031,0x8c428004,0x03e00008,0],
                [0x3c020000,0x8c420000,0x03e00008,0],rels)
            self.assertFalse(engine.relocations(obj,code,start,want,engine.place(self.name))['passed'])

    def test_missing_gp_is_not_inferred_from_a_symbol_name(self):
        engine=Engine(self.root)
        with self.assertRaises(ValueError):engine.validated_gp('main')
        self.assertIsNone(engine.symbol_address('D_00300000','main'))

    def test_manifest_cannot_escape_workspace_inputs(self):
        self.settings['programs']['main']['elf']='../other.elf';self.save()
        with self.assertRaises(ValueError):Engine(self.root)

    def test_report_translation_preserves_code_and_evidence_paths(self):
        from presentation_language import markdown
        text='Funktion wählen\n`C:/Funktion/Quelle.c`\n```json\n{"Funktion":"Quelle"}\n```\n'
        translated=markdown(text,'en')
        self.assertTrue(translated.startswith('Select function'))
        self.assertIn('`C:/Funktion/Quelle.c`',translated)
        self.assertIn('{"Funktion":"Quelle"}',translated)

    def test_pal_adapter_cannot_write_public_comments(self):
        from RAC4_PAL import public_reservations as public
        with self.assertRaises(public.ReservationError):public._api('PATCH','unused',{'body':'forbidden'})


if __name__=='__main__':unittest.main()
