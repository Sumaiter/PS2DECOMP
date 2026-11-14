"""Optional unchanged PS2 compiler checks on synthetic index-base examples.

No ROM or game source is used. The command is a JSON argv array with source
and object placeholders. Logs and objects stay in the selected local output.
"""
import argparse
import hashlib
import json
import os
import io
import struct
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compile_rule_examples import object_info
from index_base_variants import generate
from elftools.elf.elffile import ELFFile

SOURCE = '''#include "common.h"
extern int selected;
extern int gate(void *);
int example(unsigned char *state) {
 if (gate(state)) {
  if (*(Element *)(state + 48) >= ((Element *)(state + 8))[selected]) return 1;
 }
 return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--command-json', type=Path, required=True)
    parser.add_argument('--cwd', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    command = json.loads(args.command_json.read_text(encoding='utf-8'))
    if (not isinstance(command, list) or not command
            or not all(isinstance(p, str) for p in command)
            or not all(any('{' + key + '}' in p for p in command) for key in ('source', 'object'))):
        raise ValueError('Expected an argv array with source and object placeholders.')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    for context, scalar in [('word', 'int'), ('halfword', 'short'),
                            ('single', 'float'), ('double', 'double')]:
        root = output / context
        (root / 'include').mkdir(parents=True)
        header = 'typedef ' + scalar + ' Element;\n'
        for p in (root / 'include/common.h', root / 'common.h'):
            p.write_text(header, encoding='utf-8', newline='\n')
        children = list(generate(SOURCE, root))
        if len(children) != 1:
            raise ValueError('Expected exactly one child in ' + context)
        pair = []
        for phase, source in [('before', SOURCE), ('after', children[0][1])]:
            path = root / (phase + '.c')
            obj = path.with_suffix('.o')
            path.write_text(source, encoding='utf-8', newline='\n')
            values = {key: Path(os.path.relpath(value, args.cwd)).as_posix()
                      for key, value in (('source', path), ('object', obj))}
            run = subprocess.run([p.format(**values) for p in command], cwd=args.cwd,
                                 capture_output=True, text=True, timeout=90)
            path.with_suffix('.log').write_text(run.stdout + run.stderr, encoding='utf-8')
            run.check_returncode()
            if 'warning:' in (run.stdout + run.stderr).lower():
                raise ValueError('Compiler warning in ' + context + '/' + phase)
            info = object_info(obj)
            relevant = [r for r in info['relocations'] if r['section'] == '.text']
            targets = sorted((r['type'], r['symbol']) for r in relevant)
            helper = [(4, 'dpcmp')] if scalar == 'double' else []
            if targets not in (sorted(helper + [(4, 'gate'), (5, 'selected'), (6, 'selected')]),
                               sorted(helper + [(4, 'gate'), (7, 'selected__gp')])):
                raise ValueError('Unexpected actual call/data relocation targets.')
            text = ELFFile(io.BytesIO(obj.read_bytes())).get_section_by_name('.text').data()
            for rel in relevant:
                word = struct.unpack_from('<I', text, rel['offset'])[0]
                rel['encodedAddend'] = word & (0x3ffffff if rel['type'] == 4 else 0xffff)
                if rel['encodedAddend'] != 0:
                    raise ValueError('Unexpected nonzero relocation addend.')
            pair.append(dict(phase=phase, sourceHash=hashlib.sha256(source.encode()).hexdigest(), **info))
        cases.append(dict(context=context, headerHash=hashlib.sha256(header.encode()).hexdigest(), builds=pair))
    report = dict(syntheticSourcesOnly=True, retailComparison=False, compilerRuns=2 * len(cases),
                  semanticApproval=False, compilerFlagsChanged=False, cases=cases)
    (output / 'evidence.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(compilerRuns=report['compilerRuns'], output=str(output))))


if __name__ == '__main__':
    main()
