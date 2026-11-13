"""Optional synthetic compiler evidence; writes local logs/objects, never game C.

Use --command-json with an argv array containing {source} and {object}.
Expected integer-to-pointer compiler warnings are recorded, not hidden.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compile_rule_examples import object_info
from test_source_fidelity_pointer import HEADERS, examples
import source_fidelity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--command-json', type=Path, required=True)
    parser.add_argument('--cwd', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    command = json.loads(args.command_json.read_text(encoding='utf-8'))
    if not isinstance(command, list) or not command or not all(isinstance(p, str) for p in command):
        raise ValueError('Expected an argv array.')
    if not all(any('{' + key + '}' in p for p in command) for key in ('source', 'object')):
        raise ValueError('Missing source/object placeholders.')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    for context, header in zip(('int-long', 'short-int'), HEADERS):
        root = output / context
        (root / 'include').mkdir(parents=True)
        for path in (root / 'common.h', root / 'include/common.h'):
            path.write_text(header, encoding='utf-8', newline='\n')
        for name, body in examples().items():
            source = '#include "common.h"\n' + body + '\n'
            path = root / (name + '.c')
            obj = path.with_suffix('.o')
            path.write_text(source, encoding='utf-8', newline='\n')
            argv = [part.format(source=str(path), object=str(obj)) for part in command]
            run = subprocess.run(argv, cwd=args.cwd, capture_output=True, text=True, timeout=45)
            log = run.stdout + run.stderr
            path.with_suffix('.log').write_text(log, encoding='utf-8', newline='\n')
            run.check_returncode()
            diagnostic = source_fidelity.inspect(source, root)
            expected = int(name in ('assignment', 'initializer'))
            if len(diagnostic['integerPointerConversions']) != expected or diagnostic['violations']:
                raise ValueError('Unexpected diagnostic: ' + name)
            info = object_info(obj)
            rels = info['relocations']
            if name == 'pointer-arithmetic':
                if not rels or any(r['symbol'] != 'table' for r in rels):
                    raise ValueError('Expected actual external table relocations.')
            elif rels:
                raise ValueError('Unexpected address relocation: ' + name)
            cases.append(dict(context=context, case=name,
                              headerHash=hashlib.sha256(header.encode()).hexdigest(),
                              sourceLFHash=hashlib.sha256(source.encode()).hexdigest(),
                              compilerWarning='warning:' in log.lower(),
                              diagnosticCount=expected, **info))
    report = dict(syntheticSourcesOnly=True, semanticApproval=False, retailComparison=False,
                  compilerRuns=len(cases), cases=cases)
    (output / 'evidence.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps(dict(compilerRuns=len(cases), output=str(output))))


if __name__ == '__main__':
    main()
