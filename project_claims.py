"""Atomic project-local claims. Never reclaim a reservation by age or PID."""
from pathlib import Path
import datetime
import re
from contextlib import contextmanager
import time


class ClaimAPI:
    def __init__(self, root):
        self.DIR = Path(root).resolve() / 'build/claims'
        # Every linked worktree must use the main repository's claim directory.
        import subprocess
        if (Path(root) / '.git').exists():
            result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                capture_output=True, text=True, check=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            expected = Path(result.stdout.strip()).resolve().parent / 'build/claims'
            if self.DIR.resolve() != expected.resolve():
                raise ValueError('Link build/claims to the main repository shared claim directory before working in a worktree.')

    def path(self, name):
        if not re.fullmatch(r'func_(?:L\d\d_)?[0-9A-F]{8}', name):
            raise ValueError('Invalid function claim')
        return self.DIR / name

    def holder(self, name):
        p = self.path(name)
        try:
            parts = p.read_text(encoding='utf-8').split()
            return parts[0] if parts else 'unreadable-reservation'
        except FileNotFoundError:
            return None

    def claim(self, owner, name):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', owner):
            raise ValueError('Invalid claim owner')
        self.DIR.mkdir(parents=True, exist_ok=True)
        p = self.path(name)
        with self.lock():
            try:
                with p.open('x', encoding='utf-8', newline='\n') as stream:
                    stream.write(owner + ' ' + datetime.datetime.now(datetime.timezone.utc).isoformat() + '\n')
                return True
            except FileExistsError:
                return self.holder(name) == owner

    def release(self, owner, name):
        # Serialize cooperating releases/claims with an OS-owned lock.
        self.DIR.mkdir(parents=True, exist_ok=True)
        with self.lock():
            if self.holder(name) == owner:
                self.path(name).unlink()
                return True
        return False

    @contextmanager
    def lock(self):
        from auto_jobs import file_lock
        deadline = time.monotonic() + 2
        while True:
            guard = file_lock(self.DIR / 'claims.guard')
            try:
                guard.__enter__()
                break
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        try:
            yield
        finally:
            guard.__exit__(None, None, None)
