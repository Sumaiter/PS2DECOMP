"""Run only local build/decompiler tools, with a bounded lifetime for their children."""
import os
import signal
import subprocess
import json
from pathlib import Path
import sys


def run(command, *, cwd, timeout, env=None, **kwargs):
    from search_time import tool_timeout
    timeout = tool_timeout(command, timeout)
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    job=None
    launched=command
    if os.name=='nt':
        from windows_job import Job
        job=Job()
        payload=[os.fspath(p) for p in command] if not isinstance(command,str) else command
        launched=[sys.executable,str(Path(__file__).with_name('process_host.py')),job.name,json.dumps(payload)]
    try:
        # Creating a host/job can itself take time. Never grant that setup time
        # again to the child, or let sequential probes each reuse an old timeout.
        timeout = tool_timeout(command, timeout)
        process = subprocess.Popen(launched, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=flags, start_new_session=os.name != 'nt', **kwargs)
        try:
            timeout = tool_timeout(command, timeout)
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:
            if job is not None:
                try:job.terminate()
                except OSError:
                    if process.poll() is None:process.kill()
            else:
                # Descendants can still own pipes after the immediate parent exits.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                # Keep cleanup bounded even if a foreign handle delays EOF. The
                # Windows job is closed below; pipe-reader threads are daemons.
                pass
            raise
    finally:
        if job is not None:job.close()
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
