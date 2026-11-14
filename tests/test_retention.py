from pathlib import Path
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
import json,tempfile,unittest
import auto_jobs as jobs
import retention


class CandidateRetention(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.name='func_00001000'

    def candidate(self,number,score,technical=False):
        directory=self.root/'build/studio'/self.name/f'20261107-120000-{number:08x}'
        directory.mkdir(parents=True)
        source=directory/'candidate.c';source.write_text(f'int {self.name}(void){{return {number};}}',encoding='utf-8')
        obj=source.with_suffix('.o');obj.write_bytes(b'object'+bytes([number]))
        result=dict(name=self.name,snapshot=str(source),sourceHash=jobs.sha(source),compiledSize=8,
                    byteMatch=technical,verdict='Code abweichend')
        jobs.write(directory/'result.json',result)
        return dict(snapshot=str(source),sourceHash=jobs.sha(source),objectHash=jobs.sha(obj),
                    score=score,technicalMatch=technical,attempt=number)

    def test_keeps_best_four_globally_and_deletes_only_registered_artifacts(self):
        records=[self.candidate(i,i*10) for i in range(1,8)]
        manual=self.root/'build/studio'/self.name/'draft.c';manual.write_text('user draft')
        plan=retention.prepare(self.root,self.name,records)
        result=retention.apply(self.root,plan)
        self.assertEqual(result['removedSnapshots'],3)
        self.assertEqual({r['score'] for r in records if Path(r['snapshot']).is_file()},{40,50,60,70})
        self.assertEqual(manual.read_text(),'user draft')

    def test_pinned_review_evidence_and_technical_match_survive(self):
        records=[self.candidate(i,i*10,i==1) for i in range(1,8)]
        plan=retention.prepare(self.root,self.name,records,protected=[records[1]['snapshot']])
        result=retention.apply(self.root,plan)
        self.assertTrue(Path(records[0]['snapshot']).is_file())
        self.assertTrue(Path(records[1]['snapshot']).is_file())
        self.assertEqual(result['additionalProtectedEvidence'],1)

    def test_path_escape_unknown_files_or_changed_hash_never_deleted(self):
        records=[self.candidate(i,i*10) for i in range(1,6)]
        extra=Path(records[0]['snapshot']).with_name('user-notes.txt');extra.write_text('keep')
        plan=retention.prepare(self.root,self.name,records)
        self.assertEqual(len(plan['deletions']),0);self.assertEqual(len(plan['skipped']),1)
        self.assertTrue(extra.is_file())
        with self.assertRaises(ValueError):retention.safe_directory(self.root,self.name,self.root/'outside/candidate.c')
        extra.unlink();plan=retention.prepare(self.root,self.name,records)
        Path(records[0]['snapshot']).write_text('changed')
        with self.assertRaises(ValueError):retention.apply(self.root,plan)
        self.assertTrue(Path(records[0]['snapshot']).is_file())

    def test_compacted_negative_hint_is_bound_and_never_a_compiler_cache_hit(self):
        records=[self.candidate(i,i*10) for i in range(1,6)];old=records[0]
        plan=retention.prepare(self.root,self.name,records);retention.apply(self.root,plan)
        receipts=retention.load(self.root,self.name)['retired']
        self.assertEqual(retention.retired_key(self.root,self.name,old,receipts),
                         jobs.source_key(f'int {self.name}(void){{return 1;}}'))
        altered=dict(old,sourceHash='0'*64)
        self.assertIsNone(retention.retired_key(self.root,self.name,altered,receipts))
        folder=self.root/'build/autodecomp'/('a'*32);archive=folder/'history/old'
        packet=archive/'jobs'/self.name;packet.mkdir(parents=True)
        (packet/'attempts.jsonl').write_text(json.dumps(old)+'\n')
        data=dict(pins={},history=[str(archive)]);jobs.write(archive/'session.json',dict(pins={}))
        self.assertEqual(len(jobs.prior_failed_sources(folder,data,self.name)),1)
        self.assertEqual(jobs.reusable_attempts(folder,data,self.name),{})

    def test_separate_runs_use_same_global_four_slots(self):
        first=[self.candidate(i,i*10) for i in range(1,5)]
        retention.apply(self.root,retention.prepare(self.root,self.name,first))
        second=[self.candidate(i,i*10) for i in range(5,8)]
        retention.apply(self.root,retention.prepare(self.root,self.name,second))
        self.assertEqual(sum(Path(r['snapshot']).exists() for r in first+second),4)

    def test_unreadable_job_and_explicit_review_reference_preserve_evidence(self):
        records=[self.candidate(i,i*10) for i in range(1,7)]
        packet=self.root/'build/autodecomp/session/jobs'/self.name
        packet.mkdir(parents=True)
        (packet/'job.json').write_text('{broken')
        (packet/'attempts.jsonl').write_text('\n'.join(json.dumps(r) for r in records))
        groups,protected,stamps=retention.inventory(self.root)
        plan=retention.prepare(self.root,self.name,list(groups[self.name].values()),protected,stamps)
        self.assertEqual(plan['deletions'],[])
        jobs.write(packet/'job.json',dict(best=records[-1]))
        proof=self.root/'build/review_example/proof.json'
        jobs.write(proof,dict(snapshot=records[0]['snapshot']))
        groups,protected,stamps=retention.inventory(self.root)
        plan=retention.prepare(self.root,self.name,list(groups[self.name].values()),protected,stamps)
        retention.apply(self.root,plan)
        self.assertTrue(Path(records[0]['snapshot']).is_file())
        self.assertFalse(Path(records[1]['snapshot']).exists())

    def test_reference_cache_rechecks_changed_and_new_proofs(self):
        proof=self.root/'build/review_example/proof.json'
        first='20261107-120000-12345678';second='20261107-120000-87654321'
        jobs.write(proof,dict(snapshot=first))
        self.assertEqual(retention.evidence_stamps(self.root),{first})
        original=Path.read_text;reads=[]
        def read(path,*args,**kwargs):
            reads.append(path);return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',read):
            self.assertEqual(retention.evidence_stamps(self.root),{first})
        self.assertNotIn(proof,reads)
        jobs.write(proof,dict(snapshot=second,changed=True))
        jobs.write(proof.with_name('new.json'),dict(snapshot=first))
        self.assertEqual(retention.evidence_stamps(self.root),{first,second})

    @unittest.skip("Original game/compiler review evidence stays in the originating local repository.")
    def test_real_search_completion_applies_four_slots_and_keeps_its_claim(self):
        from tests import test_retry_incomplete as fixture_module
        fixture=fixture_module.ReleasedRetry(methodName='runTest');fixture.setUp()
        self.addCleanup(fixture.tearDown)
        name='func_00001000';source='int f(void){return 0;}'
        session=fixture.manager.create([name],{'maxAttempts':7},seeds={name:source})
        built=[]
        def compare(n,text,**kwargs):
            i=len(built)+1;built.append(text)
            directory=fixture.root/'build/studio'/n/f'20261107-120000-{i:08x}'
            directory.mkdir(parents=True);p=directory/'candidate.c';p.write_text(text,encoding='utf-8')
            p.with_suffix('.o').write_bytes(b'object'+bytes([i]))
            return dict(name=n,snapshot=str(p),sourceHash=jobs.sha(p),compiledSize=8,
                        byteMatch=False,verdict='Code abweichend',testScore=i*10)
        fixture.engine.compare=compare
        class Rules:
            def generate(self,text):
                i=int(text.split('return ')[1].split(';')[0])
                return [('next',f'int f(void){{return {i+1};}}')] if i<8 else []
        with ExitStack() as stack:
            stack.enter_context(patch('auto_jobs.AutoEngine',return_value=fixture.engine))
            stack.enter_context(patch('auto_jobs.Variants',return_value=Rules()))
            stack.enter_context(patch('auto_jobs.similarity',side_effect=lambda r:r['testScore']))
            stack.enter_context(patch('auto_jobs.object_key',side_effect=lambda p,e:jobs.sha(p)))
            for module in ['scheduler_variants','address_variants','review_variants','index_base_variants','terminal_return_variants',
                           'reset_store_variants','counter_relay_variants','postcall_default_variants',
                           'adjacent_input_store_variants','shared_call_join_variants']:
                stack.enter_context(patch('auto_jobs.'+module+'.generate',return_value=[]))
            for helper in ['native_guard_children','native_clear_children','word_base_children']:
                stack.enter_context(patch('auto_jobs.'+helper,return_value=[]))
            jobs.search_job(fixture.manager,session['id'],name)
        self.assertEqual(len(built),7)
        job=jobs.read(fixture.manager.folder(session['id'])/'jobs'/name/'job.json')
        self.assertEqual(job['retention']['removedSnapshots'],3)
        self.assertTrue(Path(job['best']['snapshot']).is_file())
        self.assertEqual(fixture.claims.holder(name),session['owner'])


if __name__=='__main__':unittest.main()
