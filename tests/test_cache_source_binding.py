"""A lossy search fingerprint cannot substitute for exact compiler provenance."""
from pathlib import Path
import tempfile,unittest
import auto_jobs as jobs
import search_time


class ExactCacheSource(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'candidate.c'
        self.source='int f(int a,int b){return a++ + b;}'
        self.path.write_text(self.source);self.path.with_suffix('.o').write_bytes(b'object')
        result=dict(snapshot=str(self.path),sourceHash=jobs.sha(self.path),compiledSize=8,byteMatch=False)
        jobs.write(self.path.with_name('result.json'),result)
        self.result=dict(result,_cacheObjectHash=jobs.sha(self.path.with_suffix('.o')),
            _cacheResultHash=jobs.sha(self.path.with_name('result.json')))

    def test_operator_boundaries_with_same_lossy_search_key_never_reuse_wrong_body(self):
        different='int f(int a,int b){return a + ++b;}'
        self.assertEqual(jobs.source_key(different),jobs.source_key(self.source))
        self.assertTrue(jobs.same_cached_source(self.result,self.source))
        self.assertFalse(jobs.same_cached_source(self.result,different))

    def test_changed_snapshot_object_or_result_invalidates_cached_evidence(self):
        for p in (self.path,self.path.with_suffix('.o'),self.path.with_name('result.json')):
            original=p.read_bytes();p.write_bytes(b'changed')
            self.assertFalse(jobs.same_cached_source(self.result,self.source))
            p.write_bytes(original);self.assertTrue(jobs.same_cached_source(self.result,self.source))

    def test_unbound_or_missing_evidence_is_never_reused(self):
        unbound=dict(self.result);unbound.pop('_cacheObjectHash')
        self.assertFalse(jobs.same_cached_source(unbound,self.source))
        self.path.unlink();self.assertFalse(jobs.same_cached_source(self.result,self.source))

    def archive(self):
        folder=Path(self.temp.name)/'build/autodecomp'/('a'*32)
        archive=folder/'history'/'explicit-pass';name='func_00100000'
        pins={str(Path(self.temp.name)/'app'/filename): {'sha256': str(i)*64}
              for i,filename in enumerate(('auto_jobs.py','project_engine.py','project_config.py','unknown_checker.py','seed_normalize.py'),1)}
        data=dict(pins=pins,history=[str(archive)])
        jobdir=archive/'jobs'/name;jobdir.mkdir(parents=True)
        jobs.write(archive/'session.json',dict(pins=pins))
        jobs.write(jobdir/'job.json',dict(timeAccounting=search_time.initial(jobs.DEFAULTS,pins)))
        record=dict(snapshot=str(self.path),sourceHash=jobs.sha(self.path),objectHash=jobs.sha(self.path.with_suffix('.o')))
        import json
        (jobdir/'attempts.jsonl').write_text(json.dumps(record)+'\n')
        return folder,archive,name,data

    def test_archived_cache_requires_every_shared_code_pin_and_exact_artifacts(self):
        folder,archive,name,data=self.archive()
        cache=jobs.reusable_attempts(folder,data,name)
        self.assertEqual(len(cache),1)
        self.assertTrue(jobs.same_cached_source(next(iter(cache.values())),self.source))
        for filename in ('project_engine.py','project_config.py','unknown_checker.py','seed_normalize.py'):
            key=next(k for k in data['pins'] if Path(k).name==filename)
            original=data['pins'][key];data['pins'][key]={'sha256':'f'*64}
            self.assertEqual(jobs.reusable_attempts(folder,data,name),{})
            data['pins'][key]=original
        for path in (self.path,self.path.with_suffix('.o'),self.path.with_name('result.json')):
            original=path.read_bytes();path.write_bytes(b'changed evidence')
            self.assertEqual(jobs.reusable_attempts(folder,data,name),{})
            path.write_bytes(original)
        self.assertEqual(len(jobs.reusable_attempts(folder,data,name)),1)

    def test_legacy_or_mismatched_archive_job_cannot_borrow_session_pin_provenance(self):
        folder,archive,name,data=self.archive();path=archive/'jobs'/name/'job.json'
        for accounting in (None, {'version':1,'inputs':'old pass inputs'}, {'version':999,'inputs':search_time.input_identity(data['pins'])}):
            jobs.write(path,dict(timeAccounting=accounting) if accounting is not None else {})
            self.assertEqual(jobs.reusable_attempts(folder,data,name),{})

    def test_project_tool_basenames_cannot_hide_changed_shared_checker_hashes(self):
        folder,archive,name,data=self.archive()
        project_tools={str(Path(self.temp.name)/'project/tools'/filename): {'sha256':'a'*64}
                       for filename in ('auto_jobs.py','project_engine.py','project_config.py')}
        data['pins'].update(project_tools)
        jobs.write(archive/'session.json',dict(pins=data['pins']))
        jobs.write(archive/'jobs'/name/'job.json',dict(timeAccounting=search_time.initial(jobs.DEFAULTS,data['pins'])))
        self.assertEqual(len(jobs.reusable_attempts(folder,data,name)),1)
        shared=str(Path(self.temp.name)/'app/project_engine.py')
        data['pins'][shared]={'sha256':'b'*64}
        self.assertEqual(jobs.reusable_attempts(folder,data,name),{})


if __name__=='__main__':unittest.main()
