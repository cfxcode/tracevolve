"""Code task provider and controlled artifact gateway. No host filesystem tool."""
from __future__ import annotations
import ast
import copy
import io
import random
import tokenize
from .contracts import Bundle
from .tasks import ResourceEnvironment, envelope
from .util import canonical, digest, text_hash

BASE_PROMPT = '''Complete the user's Python maintenance issue using the available repository tools.
Inspect the implementation and public examples, update the affected code while preserving
its documented interfaces, and run appropriate checks before submitting the final files.
Follow the full issue requirements, including edge cases. Report honestly if blocked.'''


def contract(version='code-maintenance-v1'):
    if version not in ('code-maintenance-v1','code-maintenance-v2'): raise ValueError('unknown code contract')
    return {'version':version,'family':'python-code-maintenance','filesystem':'allowlisted-task-files',
            'write_policy':'replace' if version.endswith('v1') else 'compare-and-swap',
            'execution':'macos-seatbelt-python-v1','test_policy':'immutable-public-plus-host-acceptance',
            'external_network':False,'max_file_bytes':32768,'submit_policy':'freeze-artifact'}


def generate(seed, source_id, split):
    from .code_corpus import ISSUES, fixture
    index=seed % len(ISSUES)
    f=fixture(index)
    content={'id':source_id,'domain':'python-code-maintenance','issue_key':f['key'],
             'issue':f['issue'],'files':f['files'],'public_tests':f['public'],'entrypoint':'api',
             'acceptance_hash':digest(f['acceptance']), 'module_mapping':{}}
    return envelope(content,source_id,split=split,seed=seed,identifier_contract='internal-python-module-names',
                    template_id=digest({'domain':content['domain'],'issue_key':f['key']}),semantic_hash=digest(content))


def plan(seed,development,units,heldout,forbidden=()):
    from .code_corpus import ISSUES
    seen=set(forbidden); result={}; index=0
    for split,count in [('development',development),('validation',units),('sealed_evaluation',units*heldout)]:
        result[split]=[]
        for _ in range(len(ISSUES)):
            if len(result[split])==count: break
            task=generate(seed+index,f'code-{seed}-{index}',split); index+=1
            if task['template_id'] in seen: continue
            seen.add(task['template_id']); result[split].append(task)
        if len(result[split])!=count: raise ValueError('CODE_CORPUS_EXHAUSTED: need distinct issue templates')
    # Frozen before any runs; guaranteed not to overlap any existing split.
    result['followup']=[]
    for _ in range(len(ISSUES)):
        task=generate(seed+index,f'code-followup-{seed}-{index}','post_release'); index+=1
        if task['template_id'] not in seen:
            result['followup']=[task]; break
    if not result['followup']: raise ValueError('CODE_CORPUS_EXHAUSTED: no independent followup')
    return result


def rename_files(files,mapping):
    out={}
    for path,content in files.items():
        tokens=list(tokenize.generate_tokens(io.StringIO(content).readline))
        # Module identifiers occur only in ImportFrom in this frozen corpus.
        tree=ast.parse(content)
        positions={(n.lineno,n.col_offset+5):mapping[n.module] for n in ast.walk(tree)
                   if isinstance(n,ast.ImportFrom) and n.module in mapping}
        tokens=[t._replace(string=positions[t.start]) if t.type==tokenize.NAME and t.start in positions else t for t in tokens]
        out[mapping.get(path[:-3],path[:-3])+'.py']=tokenize.untokenize(tokens)
    return out


def rename(task,seed):
    content=copy.deepcopy(task['content']); rng=random.Random(seed)
    mapping={name[:-3]:'module_'+format(rng.getrandbits(48),'012x') for name in sorted(content['files'])}
    content['files']=rename_files(content['files'],mapping)
    content['entrypoint']=mapping[content['entrypoint']]
    content['module_mapping']=mapping
    content['id']+='-renamed'
    return envelope(content,task['source_id'],identifier_contract=task['identifier_contract'],
                    template_id=task['template_id']),mapping


def tool_specs(c):
    from .task_server import schema
    string={'type':'string'}
    writes={'path':string,'content':string}
    if c['write_policy']=='compare-and-swap': writes['expected_sha256']=string
    rows=[('load_bundle','Read installed instructions and optional Skill before any repository operation.',{}),
          ('list_files','List task files and hashes, full maintenance issue and immutable public examples.',{}),
          ('read_file','Read one task source file and its current SHA-256.',{'path':string}),
          ('write_file','Replace an existing task source file (max 32 KiB). '+
           ('Requires current content SHA-256; stale writes do not mutate files.' if len(writes)==3 else 'Whole file replacement.'),writes),
          ('run_tests','Run immutable public examples in an isolated Python process. A test failure is diagnostic, not a gateway error.',{}),
          ('check_examples','Run your own additional JSON input/expected-output examples (1..32). Does not replace immutable tests or reveal hidden cases.',
           {'cases':{'type':'array','minItems':1,'maxItems':32,'items':schema({'input':{},'expected':{}})}}),
          ('submit','Freeze current task files for independent host acceptance. No further file changes are possible.',{})]
    return [{'name':n,'description':d,'inputSchema':schema(p),
             'annotations':{'readOnlyHint':n in ('load_bundle','list_files','read_file'),'destructiveHint':False,'openWorldHint':False}}
            for n,d,p in rows]


class CodeEnvironment(ResourceEnvironment):
    def __init__(self,task,contract,max_calls=64):
        if task.get('domain')!='python-code-maintenance' or contract.get('family')!='python-code-maintenance':
            raise ValueError('code task/contract mismatch')
        self.task=copy.deepcopy(task); self.contract=contract; self.files=copy.deepcopy(task['files'])
        self.calls=self.errors=0; self.loaded=self.submitted=False; self.max_calls=max_calls
        self.public_checks=[]

    def _dispatch(self,name,args):
        expected={s['name']:set(s['inputSchema']['required']) for s in tool_specs(self.contract)}
        if self.submitted: return {'error':'ALREADY_SUBMITTED'}
        if name=='load_bundle' or not isinstance(args,dict) or name not in expected or set(args)!=expected[name]: return {'error':'INVALID_ARGUMENT'}
        if name=='list_files': return {'files':{p:text_hash(v) for p,v in self.files.items()},'issue':self.task['issue'],'public_tests':self.task['public_tests'],'entrypoint':self.task['entrypoint']}
        if name in ('read_file','write_file'):
            path=args['path']
            if not isinstance(path,str) or path not in self.files: return {'error':'UNKNOWN_FILE'}
            if name=='read_file': return {'path':path,'content':self.files[path],'sha256':text_hash(self.files[path])}
            if not isinstance(args['content'],str) or len(args['content'].encode())>32768: return {'error':'INVALID_CONTENT'}
            if self.contract['write_policy']=='compare-and-swap' and args['expected_sha256']!=text_hash(self.files[path]): return {'error':'STALE_WRITE'}
            self.files[path]=args['content']
            return {'path':path,'sha256':text_hash(self.files[path])}
        if name in ('run_tests','check_examples'):
            from .code_runner import check
            cases=self.task['public_tests'] if name=='run_tests' else args['cases']
            if name=='check_examples':
                if not isinstance(cases,list) or not 1<=len(cases)<=32 or any(not isinstance(c,dict) or set(c)!={'input','expected'} for c in cases):
                    return {'error':'INVALID_CASES'}
                try:
                    if len(canonical(cases).encode())>65536: return {'error':'INVALID_CASES'}
                except (ValueError,TypeError): return {'error':'INVALID_CASES'}
            result=check(self.files,self.task['entrypoint'],cases)
            self.public_checks.append(result)
            return result
        self.submitted=True
        return {'submitted':True,'artifact_sha256':digest(self.files)}

    def evaluate(self):
        # Final independent acceptance is added by CodexAdapter after exec exits.
        return {'success':False,'utility':0.0,'submitted':self.submitted,'tool_calls':self.calls,
                'tool_errors':self.errors,'agent_errors':self.errors,'environment_faults':0,
                'artifact':copy.deepcopy(self.files),'artifact_sha256':digest(self.files),'public_checks':self.public_checks}


def acceptance(task,artifact):
    from .code_corpus import ISSUES, fixture
    from .code_runner import check, execute
    f=fixture(next(i for i,row in enumerate(ISSUES) if row[0]==task['issue_key']))
    if digest(f['acceptance'])!=task['acceptance_hash']: raise ValueError('acceptance cases drifted')
    result=check(artifact,task['entrypoint'],f['acceptance'])
    # API migration additionally checks the new internal callable signature and
    # consumer dispatch using a replacement collaborator; outputs alone are insufficient.
    if f['key']=='api_migration':
        mapping=task.get('module_mapping',{})
        core=mapping.get('core','core'); consumer=mapping.get('consumer','consumer'); legacy=mapping.get('legacy','legacy')
        probe='''import inspect
from CORE import lookup
import CONSUMER, LEGACY

def handle(data):
    parameters=inspect.signature(lookup).parameters
    if parameters['key'].kind!=inspect.Parameter.KEYWORD_ONLY: return False
    if lookup({'a':None},key='a')!={'found':True,'value':None}: return False
    if lookup({},key='x',default=7)!={'found':False,'value':7}: return False
    CONSUMER.lookup=lambda *a,**k: {'found':False,'value':987}
    LEGACY.lookup=lambda *a,**k: {'found':False,'value':987}
    return CONSUMER.get_value({'records':{'a':1},'key':'a'})==987 and LEGACY.contains({'records':{'a':1},'key':'a'}) is False
'''.replace('CORE',core).replace('CONSUMER',consumer).replace('LEGACY',legacy)
        extra=check({**artifact,'acceptance_probe.py':probe},'acceptance_probe',[{'input':None,'expected':True}])
        result['passed']+=extra['passed']; result['total']+=extra['total']; result['api_contract_check']=extra['passed']==extra['total']
    return result


def certify(frozen, max_calls):
    from .code_corpus import ISSUES, fixture, corpus_identity
    from .util import now
    rows=[]
    for split,tasks in frozen['tasks'].items():
        for task in tasks:
            f=fixture(next(i for i,r in enumerate(ISSUES) if r[0]==task['content']['issue_key']))
            good=acceptance(task['content'],f['reference_files'])
            bad=acceptance(task['content'],f['files'])
            bound=len(f['reference_files'])+4  # load, list, writes, public tests, submit
            if good['passed']!=good['total'] or bad['passed']==bad['total'] or bound>max_calls:
                raise ValueError('invalid code reference fixture or call bound')
            row={'split':split,'task_sha256':task['sha256'],'template_id':task['template_id'],
                 'reference_artifact_sha256':digest(f['reference_files']),
                 'reference_checks':good,'initial_checks':bad,'reference_gateway_calls':bound}
            if split=='validation':
                other,mapping=rename(task,frozen['identity']['config']['seed']+10000)
                checks=acceptance(other['content'],rename_files(f['reference_files'],mapping))
                if checks['passed']!=checks['total']: raise ValueError('renamed reference failed')
                row['renamed_reference_checks']=checks
            rows.append(row)
    return {'at':now(),'frozen_sha256':digest(frozen),'corpus_sha256':corpus_identity(),'tasks':rows,
            'scope':'host-only offline reference; not a model result or optimization gain'}
