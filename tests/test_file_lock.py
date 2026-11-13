"""OS contention, cleanup failures and retained claim ownership; temporary files only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import auto_jobs
from auto_jobs import file_lock
from project_claims import ClaimAPI


def _compete(path, start, release, outcomes):
    try:
        start.wait(timeout=20)
        with file_lock(path):
            outcomes.put('acquired')
            if not release.wait(timeout=30):
                raise RuntimeError('Test release deadline')
    except ValueError:
        outcomes.put('busy')
    except Exception as exc:
        outcomes.put(type(exc).__name__)


def _crash_while_locked(path, ready):
    with file_lock(path):
        ready.send('acquired')
        ready.close()
        os._exit(0)


class Stream:
    def __init__(self, stream, events, state, fail_write=False, fail_flush=False):
        self.stream, self.events, self.state = stream, events, state
        self.fail_write = fail_write
        self.fail_flush = fail_flush

    @property
    def closed(self):
        return self.stream.closed

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.events.append('close')
        self.stream.close()

    def fileno(self):
        return self.stream.fileno()

    def seek(self, offset):
        return self.stream.seek(offset)

    def write(self, data):
        self.events.append('write')
        if not self.state['locked'] or self.fail_write:
            raise PermissionError('Unprotected or failed initialization')
        return self.stream.write(data)

    def flush(self):
        if self.fail_flush:
            self.events.append('failed-flush')
            raise OSError('Flush failed')
        return self.stream.flush()


class FileLockTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'claims.guard'

    @contextmanager
    def windows_stub(self, *, busy=False, fail_write=False, fail_flush=False, fail_unlock=False):
        events, state = [], dict(locked=False)
        stream = Stream(self.path.open('a+b', buffering=0), events, state,
                        fail_write=fail_write, fail_flush=fail_flush)
        def locking(fd, mode, count):
            self.assertEqual(count, 1)
            self.assertEqual(stream.stream.tell(), 0)
            if mode == 1:
                events.append('acquire')
                if busy:
                    raise OSError('Contended')
                state['locked'] = True
            else:
                events.append('unlock')
                if fail_unlock:
                    raise OSError('Unlock failed')
                state['locked'] = False
        crt = SimpleNamespace(locking=locking, LK_NBLCK=1, LK_UNLCK=2)
        with patch.object(auto_jobs, 'os', SimpleNamespace(name='nt', fstat=os.fstat)), \
                patch.dict(sys.modules, msvcrt=crt), patch.object(Path, 'open', return_value=stream):
            yield stream, events

    def test_empty_guard_is_initialized_only_after_lock_and_keeps_legacy_format(self):
        with self.windows_stub() as (stream, events):
            with file_lock(self.path):
                events.append('body')
                self.assertFalse(stream.closed)
        self.assertEqual(events, ['acquire', 'write', 'body', 'unlock', 'close'])
        self.assertTrue(stream.closed)
        self.assertEqual(self.path.read_bytes(), b'0')

    def test_busy_acquisition_never_writes_and_closes_handle(self):
        with self.windows_stub(busy=True) as (stream, events):
            with self.assertRaises(ValueError):
                with file_lock(self.path):
                    self.fail('Busy lock entered the critical section')
        self.assertEqual(events, ['acquire', 'close'])
        self.assertTrue(stream.closed)
        self.path.unlink()

    def test_initialization_failure_unlocks_and_closes_before_propagating(self):
        with self.windows_stub(fail_write=True) as (stream, events):
            with self.assertRaises(PermissionError):
                with file_lock(self.path):
                    self.fail('Failed initialization entered the critical section')
        self.assertEqual(events, ['acquire', 'write', 'unlock', 'close'])
        self.assertTrue(stream.closed)
        self.path.unlink()

    def test_unlock_failure_still_closes_handle(self):
        with self.windows_stub(fail_unlock=True) as (stream, events):
            with self.assertRaisesRegex(OSError, 'Unlock failed'):
                with file_lock(self.path):
                    pass
        self.assertEqual(events[-2:], ['unlock', 'close'])
        self.assertTrue(stream.closed)
        self.path.unlink()

    def test_initialization_flush_error_cannot_leave_a_buffered_write_after_unlock(self):
        with self.windows_stub(fail_flush=True) as (stream, events):
            with self.assertRaisesRegex(OSError, 'Flush failed'):
                with file_lock(self.path):
                    self.fail('Failed initialization entered the critical section')
        self.assertEqual(events, ['acquire', 'write', 'failed-flush', 'unlock', 'close'])
        self.assertTrue(stream.closed)
        self.assertEqual(self.path.read_bytes(), b'0')
        self.path.unlink()

    def test_body_exception_releases_real_os_lock_without_replacing_guard(self):
        self.path.write_bytes(b'legacy')
        identity = self.path.stat().st_ino
        with self.assertRaisesRegex(RuntimeError, 'body'):
            with file_lock(self.path):
                raise RuntimeError('body')
        with file_lock(self.path):
            pass
        self.assertEqual(self.path.stat().st_ino, identity)
        self.assertEqual(self.path.read_bytes(), b'legacy')

    def test_posix_branch_uses_nonblocking_flock_and_closes_on_contention(self):
        events = []
        def flock(stream, mode):
            events.append(mode)
            if mode == 3:
                raise OSError('Contended')
        fcntl = SimpleNamespace(flock=flock, LOCK_EX=1, LOCK_NB=2, LOCK_UN=4)
        with patch.object(auto_jobs, 'os', SimpleNamespace(name='posix', fstat=os.fstat)), \
                patch.dict(sys.modules, fcntl=fcntl):
            with self.assertRaises(ValueError):
                with file_lock(self.path):
                    self.fail('Busy flock entered')
        self.assertEqual(events, [3])
        self.path.unlink()

    def test_eight_real_processes_have_one_winner_and_no_failed_write_or_leaked_handle(self):
        ctx = multiprocessing.get_context('spawn')
        start, release, outcomes = ctx.Barrier(8), ctx.Event(), ctx.Queue()
        processes = [ctx.Process(target=_compete, args=(str(self.path), start, release, outcomes))
                     for _ in range(8)]
        try:
            for process in processes:
                process.start()
            rows = [outcomes.get(timeout=25) for _ in processes]
            self.assertEqual(rows.count('acquired'), 1, rows)
            self.assertEqual(rows.count('busy'), 7, rows)
        finally:
            release.set()
            for process in processes:
                process.join(timeout=10)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=10)
            outcomes.close()
            outcomes.join_thread()
        self.assertTrue(all(process.exitcode == 0 for process in processes))
        self.assertEqual(self.path.read_bytes(), b'0')
        self.path.unlink()

    def test_concurrent_foreign_release_and_claim_cannot_take_automatic_owner(self):
        api = ClaimAPI(self.directory.name)
        name, owner = 'func_00100000', 'ps2decomp-auto-active'
        self.assertTrue(api.claim(owner, name))
        start = threading.Barrier(8)
        def compete(index):
            start.wait(timeout=10)
            return api.release('foreign', name) if index % 2 else api.claim('foreign', name)
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(compete, range(8))), [False] * 8)
        self.assertEqual(api.holder(name), owner)
        with ThreadPoolExecutor(max_workers=8) as pool:
            released = list(pool.map(lambda _: api.release(owner, name), range(8)))
        self.assertEqual(released.count(True), 1)
        self.assertIsNone(api.holder(name))

    def test_process_crash_releases_os_guard_without_deleting_it(self):
        ctx = multiprocessing.get_context('spawn')
        receiving, sending = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_crash_while_locked, args=(str(self.path), sending))
        process.start()
        sending.close()
        try:
            self.assertTrue(receiving.poll(timeout=20))
            self.assertEqual(receiving.recv(), 'acquired')
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
            with file_lock(self.path):
                pass
            self.assertEqual(self.path.read_bytes(), b'0')
        finally:
            receiving.close()
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)


if __name__ == '__main__':
    unittest.main()
