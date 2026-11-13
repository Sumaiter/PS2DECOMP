"""Validate configured adapter/inputs without claiming or compiling functions."""
import argparse
from auto_engine import AutoEngine
import project_config


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', required=True)
    args = ap.parse_args()
    engine = AutoEngine(args.repo)
    if engine.config.get('adapter', 'generic') == 'generic':
        project_config.verified_inputs(engine.root, engine.config)
        for program in engine.config['programs']:
            engine.assert_reference(program)
    print('PS2DECOMP: configuration loaded; ' + str(len(engine.places)) + ' catalogued functions.')
    print('No search started. Verify function boundaries, ABI, all placements and negative controls before use.')


if __name__ == '__main__':
    main()
