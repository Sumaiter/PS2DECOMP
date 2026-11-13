"""A lossy search fingerprint cannot substitute for exact compiler provenance."""
from pathlib import Path
import tempfile,unittest
import auto_jobs as jobs


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


if __name__=='__main__':unittest.main()
