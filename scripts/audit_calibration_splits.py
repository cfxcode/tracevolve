"""Audit recorded calibration templates and certified followups; no model calls."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracevolve.store import Store
from tracevolve.tasks import generate, semantic_identity
from tracevolve.util import read_json, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', action='append', required=True)
    parser.add_argument('--root', default='.tracevolve')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    names = [s['study'] for path in args.data for s in read_json(Path(path))['studies']]
    if len(set(names)) != len(names):
        raise ValueError('duplicate study inputs')
    seen, collisions, count, followups = {}, [], 0, 0
    for name in names:
        root = Path(args.root) / name
        if not (root / 'state.sqlite').is_file():
            raise ValueError('missing study: ' + name)
        store = Store(root)
        try:
            store.verify()
            frozen = store.get(store.state('frozen'))
            config, splits = frozen['identity']['config'], dict(frozen['tasks'])
            if config['phase'] == 'p1a':
                task = generate(config['seed'] + 30000, f"followup-{config['seed']}", 'post_release', config['task_profile'])
                ref = store.state('reference_certificate')
                if not ref or not any(r['task_sha256'] == task['sha256'] and r['split'] == 'post_release_and_revalidation'
                                      for r in store.get(ref)['tasks']):
                    raise ValueError('followup certificate missing or current generator differs from archived source')
                splits['post_release_and_revalidation'] = [task]
                followups += 1
            for split, tasks in splits.items():
                for task in tasks:
                    key = semantic_identity(task['content'], False)
                    if key != task['template_id']:
                        raise ValueError('recorded template identity differs from task')
                    label = {'study':name, 'split':split, 'source_id':task['source_id']}
                    if key in seen:
                        collisions.append([seen[key], label])
                    seen[key] = label
                    count += 1
        finally:
            store.close()
    report = {'studies':names, 'frozen_tasks_checked':count-followups,
              'additional_followup_tasks_checked':followups, 'template_collisions':collisions,
              'scope':'actual recorded template IDs across these studies, including certified followups; no claim about all possible graphs'}
    write_json(Path(args.output), report)
    print({'tasks_checked':count, 'collisions':len(collisions)})
    return int(bool(collisions))


if __name__ == '__main__':
    raise SystemExit(main())
