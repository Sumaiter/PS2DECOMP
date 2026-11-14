"""Optional compiler evidence for helper diagnostics, using synthetic sources only."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compile_rule_examples import object_info
from test_source_fidelity_helpers import HEADERS, examples
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
    for context, header in zip(('int-float-byte-pointer', 'short-double-void-pointer'), HEADERS):
        root = output / context
        (root / 'include').mkdir(parents=True)
        for path in (root / 'common.h', root / 'include/common.h'):
            path.write_text(header, encoding='utf-8', newline='\n')
        for name, body in examples().items():
            source = '#include "common.h"\n' + body + '\n'
            path = root / (name + '.c')
            obj = path.with_suffix('.o')
            path.write_text(source, encoding='utf-8', newline='\n')
            values = {key: Path(os.path.relpath(value, args.cwd)).as_posix()
                      for key, value in (('source', path), ('object', obj))}
            argv = [part.format(**values) for part in command]
            run = subprocess.run(argv, cwd=args.cwd, capture_output=True, text=True, timeout=90)
            log = run.stdout + run.stderr
            path.with_suffix('.log').write_text(log, encoding='utf-8', newline='\n')
            run.check_returncode()
            result = source_fidelity.inspect(source, root)
            violations = int(name == 'literal-in-helper')
            conversions = int(name == 'integer-in-helper')
            if (not result['astAnalyzed'] or len(result['violations']) != violations
                    or len(result['integerPointerConversions']) != conversions
                    or result['semanticApproval']):
                raise ValueError('Unexpected diagnostic: ' + name)
            info = object_info(obj)
            if name == 'external-table':
                if not info['relocations'] or {r['symbol'] for r in info['relocations']} != {'table'}:
                    raise ValueError('Expected actual table relocations.')
            elif name != 'floating-helper' and info['relocations']:
                raise ValueError('Literal/integer addresses have no external relocation.')
            cases.append(dict(context=context, case=name,
                              headerHash=hashlib.sha256(header.encode()).hexdigest(),
                              sourceLFHash=hashlib.sha256(source.encode()).hexdigest(),
                              compilerWarning='warning:' in log.lower(),
                              violations=violations, integerPointerConversions=conversions, **info))
    report = dict(syntheticSourcesOnly=True, semanticApproval=False,
                  retailComparison=False, sourceRewriting=False, compilerRuns=len(cases), cases=cases)
    (output / 'evidence.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps(dict(compilerRuns=len(cases), output=str(output))))


if __name__ == '__main__':
    main()
