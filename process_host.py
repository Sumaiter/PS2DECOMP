"""Join the controller's Windows job before starting a local compiler/decompiler."""
import json,os,subprocess,sys,traceback
import windows_job


def main():
    name,encoded=sys.argv[1:]
    windows_job.join(name)
    try:
        code=subprocess.call(json.loads(encoded),stdin=sys.stdin,stdout=sys.stdout,stderr=sys.stderr,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    except BaseException:
        traceback.print_exc();code=125
    # Also reclaim descendants whose immediate parent exited normally. They may
    # still own stdout/stderr handles; waiting for EOF must never hang the queue.
    sys.stdout.flush();sys.stderr.flush()
    windows_job.finish(name,code)
    os._exit(code)


if __name__=='__main__':main()
