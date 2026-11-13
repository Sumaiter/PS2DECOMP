"""Real persistent queue with an explicitly synthetic compiler/reference."""
from pathlib import Path
from unittest.mock import patch
import json
import unittest

from tests import test_project_engine as fixture
import auto_jobs


class BoundedPrivateLocalSearch(unittest.TestCase):
    def test_proven_child_precedes_generic_variants_inside_two_build_budget(self):
        project=fixture.ProjectTests(methodName='runTest');project.setUp()
        self.addCleanup(project.doCleanups)
        compiler=project.root/'tools/compile.py'
        compiler.write_text('''from pathlib import Path
import sys,struct
source,output=sys.argv[1:3]
text=Path(source).read_text()
value=42 if 'acc = acc * acc' in text else 41
blob=bytearray(Path(__file__).with_name('template.o').read_bytes())
struct.pack_into('<I',blob,56,0x24020000|value)
Path(output).write_bytes(blob)
''',encoding='utf-8')
        project.settings['inputs']['tools/compile.py']=project.sha('tools/compile.py');project.save()
        src='#include "common.h"\nint '+project.name+'(int input){int tmp;int acc;tmp=input;acc=tmp*tmp;return acc;}'
        manager=auto_jobs.Manager(project.root)
        session=manager.create([project.name],dict(workers=1,maxAttempts=2,seconds=20),seeds={project.name:src})
        options=dict(session['options'])
        # Many ordinary children cannot displace the private-local candidate.
        original=auto_jobs.Variants.generate
        def broad(self,source,*args,**kwargs):
            for n in range(96):
                yield 'synthetic-broad-'+str(n), '#include "common.h"\nint '+project.name+'(void){return '+str(n)+';}'
            yield from original(self,source,*args,**kwargs)
        with patch.object(auto_jobs.Variants,'generate',broad):
            auto_jobs.search_job(manager,session['id'],project.name)
        final=auto_jobs.read(manager.folder(session['id'])/'jobs'/project.name/'job.json')
        self.assertEqual(final['attempts'],2)
        self.assertTrue(final['best']['technicalMatch'])
        self.assertEqual(final['best']['rule'],'private-scalar-definition-retarget')
        self.assertEqual(auto_jobs.read(manager.folder(session['id'])/'session.json')['options'],options)
        self.assertNotEqual(final['status'],'handed_off')


if __name__=='__main__':unittest.main()
