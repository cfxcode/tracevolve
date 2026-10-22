"""Bounded, baseline-only difficulty calibration, separate from candidate discovery."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracevolve.codex import CodexAdapter
from tracevolve.config import Config
from tracevolve.store import Store
from tracevolve.study import Study
from tracevolve.util import canonical, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--study', required=True)
    args = parser.parse_args()
    config = Config.load(args.config)
    if config.max_sessions != config.max_development_tasks or config.max_sessions > 6:
        raise ValueError('calibration requires a fixed baseline-only budget of at most 6 sessions')
    store = Store(args.study)
    try:
        study = Study(store, CodexAdapter(), config)
        frozen = study.freeze()
        rows = []
        for i, task in enumerate(frozen['tasks']['development']):
            run = study._run('calibration-' + str(i), study.base, task, 'difficulty_calibration')
            rows.append({'run_id': run['run_id'], 'status': run['status'], 'task_sha256': task['sha256'],
                         'evaluation': run.get('evaluation'), 'error': run.get('error')})
            report = {'scope': 'baseline-only calibration; excluded from formal candidate discovery',
                      'runs': rows, 'accounting': store.accounting(), 'integrity': store.verify()}
            write_json(store.root / 'calibration.json', report)
            print(canonical(rows[-1]), flush=True)
            if run['status'] != 'completed':
                raise RuntimeError('calibration infrastructure failure; stop without task selection')
        store.set('phase', 'CALIBRATION_FINISHED')
    finally:
        store.close()


if __name__ == '__main__':
    main()
