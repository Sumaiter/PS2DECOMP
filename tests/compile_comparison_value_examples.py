"""Optional real-compiler checks on independent, synthetic comparison sources.

Supply an unchanged local compiler wrapper as a JSON argv array containing
{source}/{object}. No ROM, game source or compiler is distributed.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comparison_value_variants import generate
from compile_rule_examples import object_info
from elftools.elf.elffile import ELFFile

SOURCE = '''#include "common.h"
extern int gate(unsigned char *);
Result example(unsigned char *state) {
 Result result;
 result = 0;
 if (gate(state)) { result = *(Element *)(state + 8) == (Element)3; }
 return result;
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
            or not all(isinstance(x, str) for x in command)
            or not all(any('{' + key + '}' in x for x in command)
                       for key in ('source', 'object'))):
        raise ValueError('Expected argv array with source and object placeholders.')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    for context, result_type, element_type in [
            ('wide-byte', 'long', 'unsigned char'),
            ('narrow-byte', 'int', 'unsigned char'),
            ('wide-halfword', 'long', 'short'),
            ('wide-single', 'long', 'float'),
            ('wide-double', 'long', 'double')]:
        root = output / context
        (root / 'include').mkdir(parents=True)
        header = 'typedef ' + result_type + ' Result;\ntypedef ' + element_type + ' Element;\n'
        for p in (root / 'include/common.h', root / 'common.h'):
            p.write_text(header, encoding='utf-8', newline='\n')
        children = list(generate(SOURCE, root))
        if len(children) != 1:
            raise ValueError('Expected one child in ' + context)
        pair = []
        for phase, source in [('before', SOURCE), ('after', children[0][1])]:
            path = root / (phase + '.c')
            obj = path.with_suffix('.o')
            path.write_text(source, encoding='utf-8', newline='\n')
            values = {k: Path(os.path.relpath(v, args.cwd)).as_posix()
                      for k, v in [('source', path), ('object', obj)]}
            run = subprocess.run([x.format(**values) for x in command], cwd=args.cwd,
                                 capture_output=True, text=True, timeout=90)
            path.with_suffix('.log').write_text(run.stdout + run.stderr, encoding='utf-8')
            run.check_returncode()
            if 'warning:' in (run.stdout + run.stderr).lower():
                raise ValueError('Compiler warning in ' + context + '/' + phase)
            info = object_info(obj)
            relocs = [x for x in info['relocations'] if x['section'] == '.text']
            expected = [(4, 'gate')] + ([(4, 'dpcmp')] if element_type == 'double' else [])
            if sorted((x['type'], x['symbol']) for x in relocs) != sorted(expected):
                raise ValueError('Unexpected actual relocation targets in ' + context)
            raw = ELFFile(io.BytesIO(obj.read_bytes())).get_section_by_name('.text').data()
            for rel in relocs:
                word = struct.unpack_from('<I', raw, rel['offset'])[0]
                rel['encodedAddend'] = word & 0x3ffffff
                if rel['encodedAddend'] != 0:
                    raise ValueError('Unexpected relocation addend.')
            pair.append(dict(phase=phase, sourceHash=hashlib.sha256(source.encode()).hexdigest(), **info))
        cases.append(dict(context=context, headerHash=hashlib.sha256(header.encode()).hexdigest(),
                          sameText=pair[0]['textHash'] == pair[1]['textHash'], builds=pair))
    report = dict(syntheticSourcesOnly=True, retailComparison=False, compilerFlagsChanged=False,
                  semanticApproval=False, compilerRuns=2 * len(cases), cases=cases)
    (output / 'evidence.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(compilerRuns=report['compilerRuns'], output=str(output))))


if __name__ == '__main__':
    main()
