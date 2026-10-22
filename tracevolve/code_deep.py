"""Compound maintenance issues for an independently frozen second smoke."""
from __future__ import annotations

PATCH = '''import copy
original=copy.deepcopy(data['document'])
doc=copy.deepcopy(original)
def tokens(path):
    if not isinstance(path,str) or (path and not path.startswith('/')): raise ValueError()
    if not path: return []
    out=[]
    for token in path[1:].split('/'):
        value=''; i=0
        while i<len(token):
            if token[i]=='~':
                if i+1>=len(token) or token[i+1] not in '01': raise ValueError()
                value+='~' if token[i+1]=='0' else '/'; i+=2
            else: value+=token[i]; i+=1
        out.append(value)
    return out
def index(token,size,adding=False):
    if token=='-' and adding: return size
    if not token or not token.isascii() or not token.isdecimal() or (len(token)>1 and token[0]=='0'): raise ValueError()
    n=int(token)
    if n<0 or n>size or (n==size and not adding): raise ValueError()
    return n
def get(root,path):
    node=root
    for token in path:
        if isinstance(node,dict): node=node[token]
        elif isinstance(node,list): node=node[index(token,len(node))]
        else: raise ValueError()
    return node
def remove(root,path):
    if not path: return None,root
    parent=get(root,path[:-1]); key=path[-1]
    if isinstance(parent,dict): value=parent.pop(key)
    elif isinstance(parent,list): value=parent.pop(index(key,len(parent)))
    else: raise ValueError()
    return root,value
def put(root,path,value,adding):
    if not path: return value
    parent=get(root,path[:-1]); key=path[-1]
    if isinstance(parent,dict):
        if not adding and key not in parent: raise ValueError()
        parent[key]=value
    elif isinstance(parent,list):
        n=index(key,len(parent),adding)
        if adding: parent.insert(n,value)
        else: parent[n]=value
    else: raise ValueError()
    return root
def equal(a,b):
    if isinstance(a,bool) or isinstance(b,bool): return type(a) is type(b) and a==b
    if isinstance(a,(int,float)) and isinstance(b,(int,float)): return a==b
    if type(a) is not type(b): return False
    if isinstance(a,list): return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
    if isinstance(a,dict): return a.keys()==b.keys() and all(equal(a[k],b[k]) for k in a)
    return a==b
for i,operation in enumerate(data['operations']):
    try:
        op=operation['op']; path=tokens(operation['path'])
        if op=='add': doc=put(doc,path,copy.deepcopy(operation['value']),True)
        elif op=='replace': doc=put(doc,path,copy.deepcopy(operation['value']),False)
        elif op=='remove': doc,_=remove(doc,path)
        elif op=='test':
            if not equal(get(doc,path),operation['value']): raise ValueError()
        elif op in ('copy','move'):
            source=tokens(operation['from'])
            value=copy.deepcopy(get(doc,source))
            if op=='move':
                if len(path)>len(source) and path[:len(source)]==source: raise ValueError()
                if path==source: continue
                doc,_=remove(doc,source)
            doc=put(doc,path,value,True)
        else: raise ValueError()
    except (ValueError,KeyError,IndexError,TypeError):
        return {'ok':False,'document':original,'failed_index':i}
return {'ok':True,'document':doc,'failed_index':None}'''

TRANSACTIONS = '''import copy
state=copy.deepcopy(data['initial']); frames=[]; output=[]
for action in data['actions']:
    op=action['op']
    if op=='begin': frames.append(copy.deepcopy(state)); output.append(True)
    elif op=='commit':
        if not frames: output.append(False)
        else: frames.pop(); output.append(True)
    elif op=='rollback':
        if not frames: output.append(False)
        else: state=frames.pop(); output.append(True)
    elif op=='set': state[action['key']]=copy.deepcopy(action['value']); output.append(True)
    elif op=='delete': output.append(action['key'] in state); state.pop(action['key'],None)
    elif op=='append':
        key=action['key']
        if key not in state or not isinstance(state[key],list): output.append(False)
        else: state[key].append(copy.deepcopy(action['value'])); output.append(True)
    elif op=='get': output.append(copy.deepcopy(state.get(action['key'])))
return {'state':state,'results':output,'open_transactions':len(frames)}'''

CACHE = '''import copy
cache={}; now=0; clock=0; loads=0; output=[]; used=0
for action in data['actions']:
    now=action['time']; op=action['op']
    for key in list(cache):
        if cache[key]['expiry']<=now: used-=cache.pop(key)['weight']
    if op=='invalidate':
        entry=cache.pop(action['key'],None)
        if entry is not None: used-=entry['weight']
        output.append(entry is not None)
    elif op=='get':
        key=action['key']; clock+=1
        if key in cache:
            entry=cache[key]; entry['recency']=clock; value=entry['value']
        else:
            loads+=1; value=copy.deepcopy(action['value']); weight=action['weight']; ttl=action['ttl']
            if ttl>0 and 0<weight<=data['capacity']:
                while used+weight>data['capacity']:
                    oldest=min(cache,key=lambda k:cache[k]['recency'])
                    used-=cache.pop(oldest)['weight']
                cache[key]={'value':value,'weight':weight,'expiry':now+ttl,'recency':clock}; used+=weight
        output.append(copy.deepcopy(value))
return {'results':output,'loads':loads,'keys':sorted(cache),'used':used}'''

RANGES = '''events={}
for start,end,weight in data:
    if start>=end or weight==0: continue
    events[start]=events.get(start,0)+weight; events[end]=events.get(end,0)-weight
points=sorted(events); value=0; out=[]
for i,point in enumerate(points[:-1]):
    value+=events[point]; end=points[i+1]
    if value==0: continue
    if out and out[-1][1]==point and out[-1][2]==value: out[-1][1]=end
    else: out.append([point,end,value])
return out'''

RESOLVE = '''import copy
raw=data; resolved={}; active=set()
def lookup(path):
    if not isinstance(path,str) or not path: raise ValueError('missing')
    if path in active: raise ValueError('cycle')
    if path in resolved: return copy.deepcopy(resolved[path])
    active.add(path)
    node=raw
    for key in path.split('.'):
        if not isinstance(node,dict) or key not in node: raise ValueError('missing')
        node=node[key]
    result=visit(node)
    active.remove(path); resolved[path]=copy.deepcopy(result)
    return copy.deepcopy(result)
def visit(node):
    if isinstance(node,dict):
        if set(node)=={'$ref'}: return lookup(node['$ref'])
        return {k:visit(node[k]) for k in sorted(node)}
    if isinstance(node,list): return [visit(v) for v in node]
    return copy.deepcopy(node)
try: return {'ok':True,'value':visit(raw),'error':None}
except ValueError as exc: return {'ok':False,'value':None,'error':str(exc)}'''


def patch_cases():
    cases=[]
    def add(doc,ops): cases.append({'document':doc,'operations':ops})
    add({'a':[0,1,2,3]},[{'op':'move','from':'/a/1','path':'/a/3'}])
    add({'a':{'n':[1]}},[{'op':'copy','from':'/a','path':'/b'},{'op':'add','path':'/b/n/-','value':2}])
    add({'a':False},[{'op':'test','path':'/a','value':0}])
    add({'a':1},[{'op':'test','path':'/a','value':1.0}])
    add({'~1':7,'a/b':2},[{'op':'test','path':'/~01','value':7},{'op':'replace','path':'/a~1b','value':3}])
    add({'a':[1]},[{'op':'add','path':'/a/-','value':2},{'op':'remove','path':'/missing'}])
    add({'a':{'b':1}},[{'op':'move','from':'/a','path':'/a/c'}])
    add({'a':1},[{'op':'move','from':'/a','path':'/a'}])
    add({'a':1},[{'op':'move','from':'/missing','path':'/missing'}])
    add({'a':[1]},[{'op':'replace','path':'','value':[3]},{'op':'add','path':'/1','value':4}])
    add({'a':1},[{'op':'remove','path':''}])
    for token in ['01','-1','+1','1.0','١','-','2']:
        add([1,2],[{'op':'remove','path':'/'+token}])
    for path in ['/a~','/a~2','a','/a/x']:
        add({'a':1},[{'op':'add','path':path,'value':2}])
    add({'a':1},[{'op':'replace','path':'/b','value':2}])
    add({'a':1},[{'op':'copy','from':'','path':'/b'}])
    add({'a':1},[{'op':'move','from':'','path':'/b'}])
    add(None,[{'op':'add','path':'','value':{'x':False}}])
    return cases


DEEP_ISSUES = [
('document_patch',
 'Repair sequential JSON document patching. Input has document and operations. Return {ok,document,failed_index}; on ANY invalid operation return the untouched original document and its zero-based failed index (atomic batch). Supported ops: add, remove, replace, copy, move, test. Each has path; copy/move have from; add/replace/test have value. Empty pointer selects the root (root removal yields null). Other pointers begin /, with ~0 decoding to ~ and ~1 to /; reject all other tilde escapes. Object keys may be empty or contain escaped slashes. Array tokens must be ASCII canonical nonnegative decimal integers without leading zero (except 0); - is allowed only for add at end. Add inserts into arrays (index <= length), replaces/creates object members; replace/remove/test require an existing target. All intermediate containers must exist. Copy deep-copies. Move reads and removes source BEFORE resolving destination, cannot move into a proper descendant, and moving an existing value to its own path is a no-op. Compare test structurally, with object order ignored, numeric 1 equal to 1.0, but booleans distinct from numbers. Unknown operations are invalid. Never mutate the request or alias copied subtrees.',
 PATCH.replace("if len(path)>len(source) and path[:len(source)]==source: raise ValueError()", "if path==source: continue").replace("return type(a) is type(b) and a==b", "return a==b").replace("n=index(key,len(parent),adding)","n=index(key,len(parent),False)").replace("'document':original,'failed_index':i", "'document':doc,'failed_index':i"),
 PATCH,
 [{'document':{'a':1},'operations':[{'op':'replace','path':'/a','value':2}]}],patch_cases()),
('nested_transactions',
 'Fix a document store with nested transactions. Input initial is a dict; actions are begin,commit,rollback,set(key,value),delete(key),append(key,value),get(key). begin saves the entire state and returns true. commit discards the innermost savepoint, preserving current state. rollback restores only the innermost savepoint. commit/rollback without a savepoint return false. set creates/replaces and returns true; delete returns whether key existed. append only works on an existing list (otherwise false, unchanged). get returns a snapshot of the value, or null if absent. Reads and nested data must never alias later mutations. Return {state,results,open_transactions}. Open transactions remain in progress at the end. Request immutable.',
 TRANSACTIONS.replace('frames.append(copy.deepcopy(state))','frames.append(state.copy())').replace("output.append(copy.deepcopy(state.get(action['key'])))", "output.append(state.get(action['key']))"),
 TRANSACTIONS,
 [{'initial':{'a':1},'actions':[{'op':'begin'},{'op':'set','key':'a','value':2},{'op':'rollback'}]}],
 [{'initial':{'a':[1]},'actions':[{'op':'get','key':'a'},{'op':'begin'},{'op':'append','key':'a','value':2},{'op':'begin'},{'op':'append','key':'a','value':3},{'op':'commit'},{'op':'rollback'},{'op':'get','key':'a'}]}, {'initial':{},'actions':[{'op':'rollback'},{'op':'commit'},{'op':'begin'},{'op':'set','key':'x','value':{'a':[1]}},{'op':'begin'},{'op':'delete','key':'x'},{'op':'rollback'},{'op':'commit'}]}, {'initial':{'x':False},'actions':[{'op':'append','key':'x','value':1},{'op':'delete','key':'missing'},{'op':'begin'},{'op':'set','key':'x','value':[]},{'op':'get','key':'x'},{'op':'append','key':'x','value':{'a':1}}]}]),
('weighted_ttl_lru',
 'Repair a weight-limited TTL LRU cache simulator. Input capacity is nonnegative integer, actions have nondecreasing integer time. Before EACH action, expire all entries with expiry<=time. get(key,value,weight,ttl) hits even when cached value is null/false/zero; hits promote recency without changing weight/expiry. A miss loads the supplied value once. Cache only if ttl>0 and 0<weight<=capacity; otherwise return it WITHOUT evicting anything. For cacheable misses evict least recently accessed entries until enough space exists. invalidate(key) removes a non-expired entry and returns whether it existed, but does not promote other entries. Return results per action, loads, sorted remaining keys, and used weight. Values are snapshots; input immutable.',
 CACHE.replace("if cache[key]['expiry']<=now", "if cache[key]['expiry']<now").replace("if ttl>0 and 0<weight<=data['capacity']:", "if ttl>0 and weight>0:").replace("while used+weight>data['capacity']:", "while cache and used+weight>data['capacity']:"),
 CACHE,
 [{'capacity':2,'actions':[{'time':0,'op':'get','key':'a','value':1,'weight':1,'ttl':3}]}],
 [{'capacity':2,'actions':[{'time':0,'op':'get','key':'a','value':None,'weight':1,'ttl':3},{'time':1,'op':'get','key':'b','value':2,'weight':1,'ttl':9},{'time':2,'op':'get','key':'a','value':9,'weight':9,'ttl':0},{'time':2,'op':'get','key':'c','value':3,'weight':1,'ttl':9},{'time':3,'op':'invalidate','key':'a'}]}, {'capacity':1,'actions':[{'time':0,'op':'get','key':'a','value':False,'weight':1,'ttl':2},{'time':1,'op':'get','key':'b','value':2,'weight':2,'ttl':3},{'time':2,'op':'get','key':'a','value':3,'weight':1,'ttl':0}]}, {'capacity':0,'actions':[{'time':0,'op':'get','key':'a','value':0,'weight':1,'ttl':2},{'time':0,'op':'invalidate','key':'a'}]}]),
('weighted_ranges',
 'Normalize weighted half-open integer ranges [start,end,weight] into sorted maximal non-overlapping segments with constant NONZERO total weight. Inputs may overlap and weights may be negative. Ignore empty/reversed intervals and zero weights. Combine all deltas at equal positions before the next segment. Omit zero-sum gaps, and merge adjacent output segments with equal weight even when an input boundary lies between them. Preserve input. Return [[start,end,total_weight],...].',
 RANGES.replace('if value==0: continue','if value<=0: continue').replace('out[-1][1]==point','out[-1][1]>point'),
 RANGES,
 [[[0,2,1],[1,3,2]]],
 [[[0,2,1],[2,4,1]], [[0,5,1],[1,4,-1],[2,3,-2]], [[1,1,9],[3,1,5],[-3,-1,-2]], [[0,2,3],[0,2,-3]], [[-1,3,2],[0,2,-1],[0,2,1]], []]),
('reference_config',
 'Resolve references in a JSON configuration tree. A dict whose EXACT key set is {$ref} is a reference to a dot-separated object-key path in the ORIGINAL root; other dicts are ordinary objects, including those with $ref plus other keys. Recurse through arrays and objects and follow chains of references. Empty/non-string reference paths or paths missing an intermediate/object key fail with missing. Cycles fail with cycle. Process object members in lexicographic key order and arrays in order; first encountered error determines the result. On success return {ok:true,value:resolved tree,error:null}; on error return {ok:false,value:null,error:missing|cycle}. Referencing null/false/zero is valid. Returned values must be independent snapshots; do not mutate input. No interpolation inside strings.',
 RESOLVE.replace("if path in active: raise ValueError('cycle')", "if path in active: return None").replace("if set(node)=={'$ref'}", "if '$ref' in node"),
 RESOLVE,
 [{'base':{'port':80},'copy':{'$ref':'base.port'}}],
 [{'a':{'$ref':'b'},'b':{'$ref':'a'}}, {'a':{'$ref':'a'}}, {'a':{'$ref':'x'}}, {'base':False,'copy':{'$ref':'base'}}, {'a':{'$ref':'','note':1}}, {'a':{'$ref':None}}, {'a':[{'$ref':'b'}, {'$ref':'c'}],'b':{'n':1},'c':{'$ref':'b.n'}}, {'a':{'$ref':'b.x'},'b':2}, {'a':{'x':{'$ref':'a'}}}])
]
