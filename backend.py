"""PS2DECOMP JSON-lines worker. Game source is never modified."""
import json
import sys
from auto_engine import AutoEngine as Engine

# Historical seed packets use this conservative flag gate. A project compiler
# command still owns its flags; arbitrary cflags comments are never executed.
SAFE_FLAGS = {'-ffast-math', '-fno-opt-stack', '-mno-split-addresses', '-mcheck-zero-division',
              '@ee29', '@lit4', '@ee29-01', '-O2', '-G0', '-Wa,-G0',
              '-fno-schedule-insns', '-fno-schedule-insns2'}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    args = ap.parse_args()
    engine = Engine(args.repo)
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            engine._progress = lambda value: print(json.dumps({'id': request.get('id'), 'progress': value}), flush=True)
            response = dict(id=request.get('id'), result=engine.dispatch(request))
        except (Exception, SystemExit) as error:
            response = dict(id=request.get('id'), error=str(error))
        print(json.dumps(response, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
