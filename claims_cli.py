"""Atomic manual claims; automatic claims require explicit application handoff."""
import argparse
from project_claims import ClaimAPI


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', required=True)
    ap.add_argument('action', choices=['claim', 'release', 'holder'])
    ap.add_argument('owner')
    ap.add_argument('function')
    args = ap.parse_args()
    api = ClaimAPI(args.repo)
    if args.action == 'holder':
        print(api.holder(args.function) or 'unclaimed')
        return
    if any(args.owner.startswith(p) for p in ('ps2decomp-auto-', 'rac3-auto-', 'rac4-auto-')):
        raise SystemExit('Use explicit application handoff for automatic reservations.')
    result = getattr(api, args.action)(args.owner, args.function)
    if not result:
        raise SystemExit('Claim operation refused; current owner: ' + str(api.holder(args.function)))
    print(args.action + ': ' + args.function)


if __name__ == '__main__':
    main()
