"""Job reads survive Windows sharing locks without hiding genuine errors."""
from pathlib import Path
import ctypes,json,os,sys,tempfile,threading,unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import auto_jobs as jobs


class JobReads(unittest.TestCase):
    def test_transient_permission_error_retries_same_file_then_parses_saved_data(self):
        original=PermissionError(13,'sharing lock','job.json')
        with patch.object(Path,'read_text',side_effect=[original,original,'{"status":"running","attempts":7}']) as reader,patch.object(jobs.time,'sleep') as sleep:
            self.assertEqual(jobs.read('job.json'),dict(status='running',attempts=7))
        self.assertEqual(reader.call_count,3)
        self.assertEqual(sleep.call_count,2)
        self.assertTrue(all(c.kwargs==dict(encoding='utf-8') for c in reader.call_args_list))

    def test_persistent_access_denial_is_bounded_and_keeps_original_error(self):
        original=PermissionError(13,'permanent denial','job.json')
        with patch.object(Path,'read_text',side_effect=original) as reader,patch.object(jobs.time,'monotonic',side_effect=[10.0,10.5,12.0]),patch.object(jobs.time,'sleep') as sleep:
            with self.assertRaises(PermissionError) as caught:jobs.read('job.json')
        self.assertIs(caught.exception,original)
        self.assertEqual(reader.call_count,2)
        sleep.assert_called_once_with(.05)

    def test_missing_and_invalid_json_are_not_hidden_or_retried(self):
        for failure in [FileNotFoundError('job.json'),'not JSON']:
            with self.subTest(failure=type(failure).__name__):
                kwargs=dict(side_effect=failure) if isinstance(failure,Exception) else dict(return_value=failure)
                with patch.object(Path,'read_text',**kwargs),patch.object(jobs.time,'sleep') as sleep:
                    with self.assertRaises(FileNotFoundError if isinstance(failure,Exception) else json.JSONDecodeError):jobs.read('job.json')
                    sleep.assert_not_called()

    @unittest.skipUnless(os.name=='nt','Native Windows sharing-lock evidence')
    def test_real_exclusive_windows_handle_is_retried_until_release(self):
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        kernel.CreateFileW.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'job.json';blob=b'{"status":"review_ready","attempts":99}\n';path.write_bytes(blob)
            # GENERIC_READ, no sharing, OPEN_EXISTING; only this test fixture.
            handle=kernel.CreateFileW(str(path),0x80000000,0,None,3,0,None)
            self.assertNotEqual(handle,ctypes.c_void_p(-1).value,ctypes.get_last_error())
            timer=None;closed=threading.Event()
            def release():
                kernel.CloseHandle(handle);closed.set()
            try:
                with self.assertRaises(PermissionError):path.read_text(encoding='utf-8')
                timer=threading.Timer(.15,release);timer.start()
                self.assertEqual(jobs.read(path),dict(status='review_ready',attempts=99))
                self.assertTrue(closed.is_set())
            finally:
                if timer is not None:timer.join()
                if not closed.is_set():release()
            self.assertEqual(path.read_bytes(),blob)

    def test_concurrent_atomic_writer_never_returns_partial_job(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'job.json';jobs.write(path,dict(attempts=0))
            errors=[]
            def writer():
                try:
                    for i in range(1,81):jobs.write(path,dict(attempts=i))
                except Exception as exc:errors.append(exc)
            worker=threading.Thread(target=writer);worker.start()
            try:
                for _ in range(160):
                    value=jobs.read(path)
                    self.assertEqual(set(value),{'attempts'})
                    self.assertTrue(0<=value['attempts']<=80)
            finally:worker.join()
            self.assertEqual(errors,[])
            self.assertEqual(jobs.read(path),dict(attempts=80))


if __name__=='__main__':unittest.main()
