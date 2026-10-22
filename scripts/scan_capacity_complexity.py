"""Offline task-design diagnostic; does not run models or generate candidates."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solve_capacity import solve
from tracevolve.tasks import generate
from tracevolve.util import source_manifest, text_hash


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--count', type=int, default=2000)
    args = parser.parse_args()
    if args.start < 0 or not 1 <= args.count <= 20000:
        raise ValueError('nonnegative start and 1..20000 tasks required')
    top = []
    for seed in range(args.start, args.start + args.count):
        task = generate(seed, 'offline', 'calibration_design', 'capacity-v1')
        reference = solve(task['content'])
        if reference['order'] is None:
            raise ValueError('unsolvable generated task')
        top.append({'seed':seed, 'states_searched':reference['states_searched'],
                    'gateway_calls':2 * len(reference['order']) + 3})
        top = sorted(top, key=lambda r:(r['states_searched'],r['seed']), reverse=True)[:10]
    print(json.dumps({'start':args.start, 'count':args.count, 'highest_search_counts':top,
                      'source':source_manifest(),
                      'solver_sha256':text_hash(Path(__file__).with_name('solve_capacity.py').read_text()),
                      'scope':'deterministic reference-search effort; not measured model difficulty'}, indent=2))


if __name__ == '__main__':
    main()
