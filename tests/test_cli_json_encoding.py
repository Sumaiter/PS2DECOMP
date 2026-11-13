"""Real CLI output through strict legacy Windows pipes, without opening a repository."""
from pathlib import Path
import json,os,subprocess,sys,unittest
import auto_jobs

class CliJsonEncoding(unittest.TestCase):
    def invoke(self,command,*,large=False,encoding='cp1252'):
        app=Path(auto_jobs.__file__).resolve().parent
        payload={'status':'completed','reason':'Entwurf → Review: 日本語 🛠',
                 'snapshot':'C:\\Spielstände\\日本語\\candidate.c','note':'äöüß',
                 'padding':'x'*(1_400_000 if large else 0)}
        script='''import sys,json
from unittest.mock import patch
sys.path.insert(0,sys.argv.pop(1))
import auto_jobs
payload=json.loads(sys.stdin.read());command=sys.argv.pop(1)
class Manager:
    def __init__(self,repo):pass
    def status(self,session):return payload
sys.argv=['auto_jobs.py',command,'--session','f'*32,'--repo','not-opened']
with patch.object(auto_jobs,'Manager',Manager),patch.object(auto_jobs,'run',return_value=None) as run:
    auto_jobs.main()
    assert run.call_count==(1 if command=='run' else 0)
'''
        result=subprocess.run([sys.executable,'-X','utf8=0','-c',script,str(app),command],
            env={**os.environ,'PYTHONUTF8':'0','PYTHONIOENCODING':encoding+':strict'},
            input=json.dumps(payload).encode('ascii'),capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr.decode('ascii',errors='backslashreplace'))
        self.assertTrue(all(b<128 for b in result.stdout))
        self.assertEqual(json.loads(result.stdout),payload)
        self.assertEqual(result.stderr,b'')

    def test_status_round_trips_unicode_under_cp1252(self):
        self.invoke('status')

    def test_completed_run_keeps_large_unicode_payload_under_cp1252(self):
        self.invoke('run',large=True)

    def test_utf8_client_receives_the_same_json_values(self):
        self.invoke('status',encoding='utf-8')

if __name__=='__main__':unittest.main()
