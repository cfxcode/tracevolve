"""Freeze and independently certify all capacity tasks before live collection."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solve_capacity import solve
from tracevolve.codex import CodexAdapter
from tracevolve.config import Config
from tracevolve.store import Store
from tracevolve.study import Study
from tracevolve.tasks import generate
from tracevolve.util import digest, now, text_hash, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--study', required=True)
    args = parser.parse_args()
    config = Config.load(args.config)
    if config.task_profile != 'capacity-v1':
        raise ValueError('capacity profile required')
    store = Store(args.study)
    try:
        if store.calls():
            raise ValueError('reference certificates must precede all live calls')
        frozen = Study(store, CodexAdapter(), config).freeze()
        rows = []
        splits = dict(frozen['tasks'])
        if config.phase == 'p1a':
            splits['post_release_and_revalidation'] = [generate(config.seed + 30000,
                f'followup-{config.seed}', 'post_release', config.task_profile)]
        for split, tasks in splits.items():
            for task in tasks:
                reference = solve(task['content'])
                if reference['order'] is None:
                    raise ValueError('unsolvable task: ' + task['source_id'])
                bound = 2 * len(reference['order']) + 3
                if bound > config.max_tool_calls:
                    raise ValueError('reference exceeds gateway budget')
                rows.append({'task_sha256':task['sha256'], 'split':split, 'gateway_calls':bound, **reference})
        certificate = {'at':now(), 'frozen_sha256':digest(frozen), 'tasks':rows,
                       'solver_sha256':text_hash(Path(__file__).with_name('solve_capacity.py').read_text()),
                       'scope':'offline reference; never sent to execution, proposal or judgment sessions'}
        store.set('reference_certificate', store.put(certificate))
        write_json(store.root / 'reference.json', certificate)
        print({'certified':len(rows), 'max_gateway_calls':max(r['gateway_calls'] for r in rows)})
    finally:
        store.close()


if __name__ == '__main__':
    main()
