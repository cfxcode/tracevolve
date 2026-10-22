"""Publishable local evidence summary, without raw prompts or authentication."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_resource_runs import replay_run
from tracevolve.store import Store
from tracevolve.util import now, read_json, write_json


def summarize(root):
    store = Store(root)
    try:
        integrity = store.verify()
        frozen_ref = store.state('frozen')
        frozen = store.get(frozen_ref)
        config = dict(frozen['identity']['config'])
        config['prior_studies'] = [Path(p).name for p in config['prior_studies']]
        rows, audits = [], []
        for call in store.calls():
            result = store.get(call['result_hash']) if call['result_hash'] else {}
            row = {'run_id':call['id'], 'phase':call['phase'], 'status':call['status'],
                   'execution_origin':result.get('execution_origin'), 'synthetic':result.get('synthetic'),
                   'request_sha256':call['request_hash'], 'result_sha256':call['result_hash'],
                   'elapsed_seconds':result.get('elapsed_seconds'), 'error':result.get('error'),
                   'raw_files':len(result.get('raw_files', {}))}
            evaluation = result.get('evaluation', {})
            for key in ('success','utility','tool_calls','tool_errors','agent_errors','environment_faults','task_origin'):
                if key in evaluation:
                    row[key] = evaluation[key]
            for key in ('acceptance','artifact_sha256'):
                if key in evaluation:
                    row[key] = evaluation[key]
            if result.get('load_evidence'):
                row['load_verified'] = result['load_evidence']['verified']
            if result:
                audits.append({'run_id':call['id'], **replay_run(store.get(call['request_hash']), result)})
            rows.append(row)
        bank_ref = store.state('judgments_sealed')
        bank = store.get(bank_ref) if bank_ref else {}
        return {'study':config['study_id'], 'config':config, 'integrity':integrity,
                'manifest_sha256':frozen_ref, 'source_archive_sha256':store.state('source_archive'),
                'reference_certificate_sha256':store.state('reference_certificate'),
                'phase':store.state('phase'), 'stop_reason':store.state('stop_reason'),
                'accounting':store.accounting(), 'runs':rows, 'transition_audits':audits,
                'candidates':[{'id':c['id'], 'component':c['component'], 'hypothesis':c['hypothesis'],
                               'source_runs':c['source_runs']} for c in store.state('candidates',[])],
                'judgments':[{'group':v['group'], 'unit_id':v['unit_id'], 'response':v['response'],
                              'error':v['error']} for v in bank.values()],
                'release_decision':store.state('release_decision'), 'lifecycle':store.state('lifecycle'),
                'report':read_json(store.root/'report.json') if (store.root/'report.json').is_file() else None}
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--study', action='append', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    studies = [summarize(root) for root in args.study]
    live = all(r['execution_origin'] == 'live_model' and r['synthetic'] is False
               for s in studies for r in s['runs']) and any(s['runs'] for s in studies)
    report = {'generated_at':now(), 'execution_origin':'live_codex_chatgpt_subscription' if live else 'mixed_or_unverified',
              'scope':'local evidence consistency, not independent remote attestation',
              'studies':studies,
              'new_real_sessions':sum(s['accounting']['execution_count'] for s in studies),
              'natural_candidates':sum(len(s['candidates']) for s in studies)}
    write_json(Path(args.output), report)
    print({'sessions':report['new_real_sessions'], 'candidates':report['natural_candidates'], 'output':args.output})


if __name__ == '__main__':
    main()
