"""Evidence validation for the separate code-maintenance experiment."""
from __future__ import annotations
import copy
from .util import digest, text_hash


def validate_evidence(data, *, synthetic=False):
    from .code_tasks import rename_files
    from .legacy import contracts
    for unit in data['units']:
        frozen = contracts._frozen(unit, 'unit')
        source = unit['source_task']
        if source['sha256'] != digest(source['content']) or source['content'].get('domain') != 'python-code-maintenance':
            raise ValueError('code source task mismatch')
        seen = set()
        for key in ('initial_evidence', 'extra_B', 'extra_C'):
            if len(unit[key]) != 1:
                raise ValueError('fixed code protocol requires one pair per channel')
            event = unit[key][0]
            task = event['task']
            if task['sha256'] != digest(task['content']):
                raise ValueError('code task hash mismatch')
            contracts._binding(event, frozen, source, 'event')
            if key != 'extra_C':
                if task != source or event['intervention'] != {'kind': 'none'}:
                    raise ValueError('initial/repeat must use unchanged task')
            else:
                mapping = event['intervention']['mapping']
                original = source['content']
                if (event['intervention']['kind'] != 'identifier_renaming'
                        or set(mapping) != {p[:-3] for p in original['files']}
                        or len(set(mapping.values())) != len(mapping)
                        or not all(isinstance(v,str) and v.isidentifier() for v in mapping.values())
                        or set(mapping) & set(mapping.values())):
                    raise ValueError('invalid code module bijection')
                expected = copy.deepcopy(original)
                expected.update(files=rename_files(original['files'],mapping), entrypoint=mapping[original['entrypoint']],
                                module_mapping=mapping, id=original['id']+'-renamed')
                if digest(expected) != task['sha256']:
                    raise ValueError('renaming changed code task semantics')
            if event.get('synthetic') and not synthetic:
                raise ValueError('synthetic evidence in live code study')
            statuses=[]
            for side in ('baseline','candidate'):
                statuses.append(contracts._run(event['observation'][side], side, frozen, task,
                                               [0,1], seen, 'event.'+side))
            completed = all(s == 'completed' for s in statuses)
            if event['valid'] != completed or event['status'] != ('completed' if completed else 'failed'):
                raise ValueError('pair status mismatch')
        for pair in unit.get('heldout',[]):
            task=pair['task']
            if task['sha256'] != digest(task['content']): raise ValueError('heldout hash mismatch')
            contracts._binding(pair,frozen,source,'heldout')
            for side in ('baseline','candidate'):
                contracts._run(pair[side],side,frozen,task,[0,1],seen,'heldout.'+side)
