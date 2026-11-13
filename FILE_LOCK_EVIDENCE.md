# Guard initialization under the OS lock

Eight workers opening a new guard could previously race while initializing its
first byte. On Windows, one worker could acquire the mandatory byte-range lock
while another still had a buffered write to byte zero. That write then raised
`PermissionError` before entering the cleanup block. Retaining the exception
also retained its open stream, which could prevent guard removal with
`WinError 32`. The same shared guard helper serves every game adapter.

`file_lock` now opens an unbuffered stream in a context manager, acquires the
existing nonblocking OS lock, and only then initializes an empty guard. It
checks the open descriptor with `fstat`, so initialization does not depend on a
separate pathname lookup. The guard still contains the existing single `0`
byte; existing nonempty files are preserved. Initialization, body and unlock
failures all close the handle. Unbuffered writes also prevent `close()` from
retrying pending initialization after the lock has been released.

Windows permits locking beyond EOF, including byte zero of an empty file.
This behavior is documented by Microsoft's
[`_locking` reference](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170)
and Python's [`msvcrt.locking` reference](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking).
POSIX continues to use `flock(LOCK_EX | LOCK_NB)`. Busy acquisition keeps the
same exception and retry behavior. The change does not delete guards, infer
ownership from age or processes, change claim ownership checks, or steal claims.

## Local evidence

All tests used temporary tool-owned files. No game inputs, function candidates,
live claims or persistent sessions were changed. No compiler behavior is
involved in this shared OS-resource fix.

- On real Windows, eight threads were synchronized after each observed an
  empty guard with the old implementation. Their append, flush and byte-lock
  operations then ran unchanged. Round 16 produced one winner, six expected
  busy exceptions and one `PermissionError` with an open stream. Keeping that
  exception alive reproduced deletion failure `WinError 32`.
- With the fix, 1,000 rounds on fresh guards used eight synchronized real
  Windows contenders. All 8,000 outcomes were one winner and seven expected
  busy exceptions per round. No unexpected exceptions or leaked handles were
  observed. The legacy guard byte and successful deletion were checked after
  every round. This is a bounded stress result, not a claim that every possible
  scheduling interleaving has been enumerated.
- `tests/test_file_lock.py` also starts eight independent processes against a
  fresh guard, verifies release after a process exits without cleanup, checks
  concurrent foreign release and claim attempts, and injects acquisition,
  initialization, flush and unlock failures. A POSIX-branch test checks
  nonblocking `flock` contention; the real-process tests run the host OS path
  on both Windows and non-Windows CI. Local native execution was on Windows.
- Existing nonempty guard contents and inode identity survive a body exception;
  subsequent acquisition succeeds. Concurrent release by the actual owner
  succeeds exactly once, while foreign owners cannot release or take its claim.

Validation commands are `python -m unittest discover -s tests -v`,
`node --test tests/*.test.js` and `python package_local.py`. The full Python
suite includes the existing eight-competitor claim regression that exposed
the original CI failure. The offline package is checked without installing it.
On baseline `02ca945`, local validation ran 398 Python tests: 390 passed and
eight optional local-evidence fixtures were skipped. All 15 JavaScript tests
and offline packaging passed.
