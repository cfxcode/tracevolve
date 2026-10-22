"""Versioned maintenance fixtures; reference implementations are host-only.

Inputs/expected values are frozen before inference. No reference solution is
included in the public task, gateway outputs or task execution directory.
"""
from __future__ import annotations
import copy
from .util import digest

# issue, broken implementation, independent reference, public inputs, extra checks
ISSUES = [
("money", "Compute invoice cents: each line is quantity * decimal unit price * (1-discount), rounded half-up to cents separately, then summed. Inputs are decimal strings; quantity is a nonnegative integer. A discount is a decimal fraction. Return integer cents.",
 "return round(sum(x['quantity'] * float(x['price']) * (1-float(x.get('discount','0'))) for x in data)*100)",
 "from decimal import Decimal, ROUND_HALF_UP\nreturn sum(int((Decimal(x['price'])*x['quantity']*(1-Decimal(x.get('discount','0')))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP)) for x in data)",
 [[{'quantity':2,'price':'1.25'}]],
 [[{'quantity':1,'price':'0.005'}]*3, [{'quantity':3,'price':'2.675','discount':'0.1'}], [], [{'quantity':0,'price':'999.99'}], [{'quantity':1,'price':'10000000000000.005'}]]),
("atomic_batch", "Apply batches of transfers to initial balances. Each batch commits all transfers in order or rolls back entirely. Reject unknown accounts, negative amounts, and insufficient funds. Self transfers require sufficient funds but have no net effect. Return balances and one Boolean per batch. Caller input must not be mutated.",
 "balances=data['balances'].copy()\naccepted=[]\nfor batch in data['batches']:\n    ok=True\n    for a,b,n in batch:\n        if a not in balances or b not in balances or n<0 or balances[a]<n:\n            ok=False; break\n        balances[a]-=n; balances[b]+=n\n    accepted.append(ok)\nreturn {'balances':balances,'accepted':accepted}",
 "balances=data['balances'].copy()\naccepted=[]\nfor batch in data['batches']:\n    trial=balances.copy(); ok=True\n    for a,b,n in batch:\n        if a not in trial or b not in trial or n<0 or trial[a]<n:\n            ok=False; break\n        trial[a]-=n; trial[b]+=n\n    if ok: balances=trial\n    accepted.append(ok)\nreturn {'balances':balances,'accepted':accepted}",
 [{'balances':{'a':10,'b':0},'batches':[[['a','b',3]]]}],
 [{'balances':{'a':10,'b':0},'batches':[[['a','b',3],['b','a',20]],[['a','b',10]]]}, {'balances':{'a':2},'batches':[[['a','a',2]],[['a','a',3]],[]]}, {'balances':{'a':5,'b':0},'batches':[[['a','b',2],['a','x',1]],[['a','b',-1]]]}]),
("ttl_cache", "Simulate a cache. Each query has time,key,value,ttl; value is the loader result for this query on a miss. A cached value is valid only while time < insertion_time+ttl. Cache null and false values too. On a miss TTL<=0 returns the loaded value without caching. Queries have nondecreasing time. Return values and total loads.",
 "cache={}; result=[]; loads=0\nfor q in data:\n    t,k=q['time'],q['key']\n    if k in cache and cache[k][1]>=t and cache[k][0]: v=cache[k][0]\n    else:\n        v=q['value']; loads+=1; cache[k]=(v,t+q['ttl'])\n    result.append(v)\nreturn {'values':result,'loads':loads}",
 "cache={}; result=[]; loads=0\nfor q in data:\n    t,k=q['time'],q['key']\n    if k in cache and t<cache[k][1]: v=cache[k][0]\n    else:\n        v=q['value']; loads+=1; cache.pop(k,None)\n        if q['ttl']>0: cache[k]=(v,t+q['ttl'])\n    result.append(v)\nreturn {'values':result,'loads':loads}",
 [[{'time':0,'key':'a','value':1,'ttl':3},{'time':1,'key':'a','value':2,'ttl':3}]],
 [[{'time':0,'key':'a','value':None,'ttl':3},{'time':1,'key':'a','value':2,'ttl':3},{'time':3,'key':'a','value':False,'ttl':0},{'time':3,'key':'a','value':4,'ttl':1}], [{'time':0,'key':'a','value':0,'ttl':2},{'time':1,'key':'a','value':9,'ttl':1}], []]),
("merge_patch", "Implement recursive JSON Merge Patch semantics: non-object patch replaces the whole target; object patch merges into an object (non-object targets first become {}); null object members delete keys; arrays replace, never merge. Preserve false, zero and empty strings. Do not mutate either input. Return the merged value.",
 "target,patch=data\nif not isinstance(patch,dict): return patch\nresult=dict(target) if isinstance(target,dict) else {}\nfor k,v in patch.items():\n    if v: result[k]=v\nreturn result",
 "import copy\ndef merge(t,p):\n    if not isinstance(p,dict): return copy.deepcopy(p)\n    r=copy.deepcopy(t) if isinstance(t,dict) else {}\n    for k,v in p.items():\n        if v is None: r.pop(k,None)\n        else: r[k]=merge(r.get(k),v)\n    return r\nreturn merge(*data)",
 [[{'a':1},{'b':2}]],
 [[{'a':{'x':1,'y':2}},{'a':{'x':None,'z':0}}], [{'a':1},{'a':False,'b':'','c':None}], [[1],{'a':{'x':None}}], [{'a':[1]}, {'a':[2,3]}], [{'a':1},None]]),
("intervals", "Normalize a list of half-open integer intervals [start,end). Discard empty or reversed intervals. Merge overlaps but keep merely adjacent intervals separate. Sort by start then end. Return a new list; do not mutate input.",
 "out=[]\nfor start,end in sorted(data):\n    if out and start<=out[-1][1]: out[-1][1]=max(out[-1][1],end)\n    else: out.append([start,end])\nreturn out",
 "out=[]\nfor start,end in sorted(x for x in data if x[0]<x[1]):\n    if out and start<out[-1][1]: out[-1][1]=max(out[-1][1],end)\n    else: out.append([start,end])\nreturn out",
 [[[3,5],[1,4]]],
 [[[1,2],[2,3]], [[3,1],[0,0],[2,4]], [[1,10],[2,3],[10,11],[9,12]], [], [[-4,-1],[-2,0],[0,2]]]),
("last_event", "Deduplicate events by id, keeping the last occurrence of each id, in the order of their last occurrence in the input. IDs can be strings or integers; 1 and '1' are distinct. Preserve whole event objects and do not mutate input.",
 "seen={}\nfor event in data: seen[str(event['id'])]=event\nreturn list(seen.values())",
 "seen=set(); out=[]\nfor event in reversed(data):\n    key=(type(event['id']).__name__,event['id'])\n    if key not in seen: seen.add(key); out.append(event)\nreturn list(reversed(out))",
 [[{'id':'a','value':1},{'id':'b','value':2}]],
 [[{'id':'a','value':1},{'id':'b','value':2},{'id':'a','value':3}], [{'id':1},{'id':'1'},{'id':1,'value':4}], [], [{'id':'','x':1},{'id':'','x':2}]]),
("topology", "Order named jobs from a dict mapping job names to dependency lists. Repeated dependencies count once. At each step choose the lexicographically smallest currently ready name. Unknown dependencies or cycles return {'error':'invalid_graph'} with no partial order; otherwise return {'order': [...]}. Input is immutable.",
 "pending={k:set(v) for k,v in data.items()}; order=[]\nwhile pending:\n    ready=sorted(k for k,v in pending.items() if not v)\n    if not ready: break\n    for k in ready:\n        order.append(k); del pending[k]\n        for v in pending.values(): v.discard(k)\nreturn {'order':order}",
 "pending={k:set(v) for k,v in data.items()}; order=[]\nif any(v-set(pending) for v in pending.values()): return {'error':'invalid_graph'}\nwhile pending:\n    ready=sorted(k for k,v in pending.items() if not v)\n    if not ready: return {'error':'invalid_graph'}\n    k=ready[0]; order.append(k); del pending[k]\n    for v in pending.values(): v.discard(k)\nreturn {'order':order}",
 [{'a':[],'b':['a']}],
 [{'a':[],'b':['a'],'z':[]}, {'a':['b'],'b':['a']}, {'a':['missing'],'b':[]}, {'a':[],'b':['a','a']}, {}]),
("pagination", "Sort records ascending by (timestamp,id), with integer timestamps and unique string IDs. Cursor is null or [timestamp,id] and is exclusive. Return at most limit records; limit is nonnegative. next_cursor is the last returned key only if MORE filtered records remain, otherwise null. No input mutation.",
 "rows=sorted(data['records'],key=lambda x:x['timestamp'])\nif data['cursor'] is not None: rows=[r for r in rows if r['timestamp']>data['cursor'][0]]\npage=rows[:data['limit']]\nreturn {'records':page,'next_cursor':[page[-1]['timestamp'],page[-1]['id']] if page else None}",
 "rows=sorted(data['records'],key=lambda x:(x['timestamp'],x['id']))\nif data['cursor'] is not None: rows=[r for r in rows if (r['timestamp'],r['id'])>tuple(data['cursor'])]\npage=rows[:data['limit']]\nreturn {'records':page,'next_cursor':[page[-1]['timestamp'],page[-1]['id']] if page and len(rows)>len(page) else None}",
 [{'records':[{'timestamp':1,'id':'a'},{'timestamp':2,'id':'b'}],'cursor':None,'limit':1}],
 [{'records':[{'timestamp':1,'id':'b'},{'timestamp':1,'id':'a'},{'timestamp':2,'id':'z'}],'cursor':[1,'a'],'limit':1}, {'records':[{'timestamp':1,'id':'a'}],'cursor':None,'limit':1}, {'records':[],'cursor':None,'limit':0}, {'records':[{'timestamp':0,'id':'x'}],'cursor':None,'limit':0}]),
("utf8_stream", "Decode UTF-8 from a list of byte chunks represented as integer arrays. Multibyte characters may span any chunk boundary, including empty chunks. Use replacement characters for invalid byte sequences and flush an incomplete trailing sequence once. Return the concatenated string.",
 "return ''.join(bytes(chunk).decode('utf-8',errors='replace') for chunk in data)",
 "import codecs\ndec=codecs.getincrementaldecoder('utf-8')('replace')\nreturn ''.join(dec.decode(bytes(chunk),final=False) for chunk in data)+dec.decode(b'',final=True)",
 [[[65,66],[67]]],
 [[[226],[130],[],[172]], [[240,159],[152],[128]], [[97,226],[130]], [[255],[65]], [], [[194],[162,194],[162]]]),
("csv_records", "Read CSV text with standard comma delimiter and double-quote escaping, including quoted newlines. The first row is the header. Skip entirely blank rows. All other rows must have exactly the header width; duplicate header names or any width mismatch returns {'error':'invalid_csv'}, never partial records. Preserve whitespace inside fields. Empty input returns {'records':[]}. Return {'records':list of dicts}.",
 "lines=[x for x in data.splitlines() if x]\nif not lines: return {'records':[]}\nheader=lines[0].split(',')\nreturn {'records':[dict(zip(header,row.split(','))) for row in lines[1:]]}",
 "import csv,io\ntry: rows=[r for r in csv.reader(io.StringIO(data,newline=''),strict=True) if r]\nexcept csv.Error: return {'error':'invalid_csv'}\nif not rows: return {'records':[]}\nheader=rows[0]\nif len(set(header))!=len(header) or any(len(r)!=len(header) for r in rows[1:]): return {'error':'invalid_csv'}\nreturn {'records':[dict(zip(header,r)) for r in rows[1:]]}",
 ['a,b\n1,2\n'],
 ['a,b\n"x,y","line1\nline2"\n', 'a,a\n1,2\n', 'a,b\n1\n', '', 'a,b\n"a""b", c \n', 'a,b\n\n1,2\n']),
("retry_policy", "Process a finite list of outcomes with max_attempts>=1. An outcome is {'status':int,'value':any,'retry_after':optional number}. Retry only 429 and 500..599, while attempts remain AND another outcome exists. Delays for retries are nonnegative retry_after when present (including zero), otherwise base_delay * 2**(attempt_number-1). Return final outcome unchanged, attempts used and delays actually waited. Empty outcomes returns final=null, attempts=0, delays=[].",
 "outcomes=data['outcomes']; delays=[]; used=0; final=None\nfor final in outcomes[:data['max_attempts']]:\n    used+=1\n    if final['status']<400: break\n    delays.append(final.get('retry_after') or data['base_delay']*2**(used-1))\nreturn {'final':final,'attempts':used,'delays':delays}",
 "outcomes=data['outcomes']; delays=[]; used=0; final=None\nfor final in outcomes[:data['max_attempts']]:\n    used+=1\n    retry=final['status']==429 or 500<=final['status']<=599\n    if not retry or used>=data['max_attempts'] or used>=len(outcomes): break\n    delay=final.get('retry_after',data['base_delay']*2**(used-1))\n    delays.append(max(0,delay))\nreturn {'final':final,'attempts':used,'delays':delays}",
 [{'outcomes':[{'status':200,'value':'ok'}],'max_attempts':3,'base_delay':2}],
 [{'outcomes':[{'status':429,'value':None,'retry_after':0},{'status':503,'value':'busy'},{'status':200,'value':'ok'}],'max_attempts':3,'base_delay':2}, {'outcomes':[{'status':404,'value':None},{'status':200,'value':1}],'max_attempts':3,'base_delay':2}, {'outcomes':[{'status':500,'value':1}],'max_attempts':4,'base_delay':1}, {'outcomes':[],'max_attempts':1,'base_delay':0}]),
("api_migration", "Migrate the internal lookup API to lookup(records, *, key, default=None), returning {'found':bool,'value':any}. A present key with null is found. Update BOTH public consumers: get_value returns the raw value or its default; contains returns only whether key exists. The handle entrypoint routes on op. Preserve public input/output shape and input immutability. Do not bypass the new lookup API in consumers.",
 "return data['records'].get(data['key'],data.get('default'))",
 "if data['op']=='contains': return data['key'] in data['records']\nreturn data['records'].get(data['key'],data.get('default'))",
 [{'op':'get_value','records':{'a':1},'key':'a'}],
 [{'op':'contains','records':{'a':None},'key':'a'}, {'op':'get_value','records':{},'key':'x','default':0}, {'op':'contains','records':{},'key':'a'}, {'op':'get_value','records':{'a':None},'key':'a','default':'fallback'}]),
]


from .code_deep import DEEP_ISSUES
ISSUES += DEEP_ISSUES


def function(body, name='solve', parameter='data'):
    return 'def '+name+'('+parameter+'):\n'+''.join('    '+line+'\n' for line in body.splitlines())


def fixture(index):
    key, issue, broken, reference, public, hidden = ISSUES[index]
    files = {'core.py':function(broken),
             'api.py':'from core import solve\n\ndef handle(data):\n    return solve(data)\n'}
    reference_files = {**files, 'core.py':function(reference)}
    if key == 'api_migration':
        files = {
            'core.py': 'def lookup(records, key, default=None):\n    return records.get(key, default)\n',
            'consumer.py': 'from core import lookup\n\ndef get_value(data):\n    return lookup(data["records"], data["key"], data.get("default"))\n',
            'legacy.py': 'from core import lookup\n\ndef contains(data):\n    return lookup(data["records"], data["key"]) is not None\n',
            'api.py': 'from consumer import get_value\nfrom legacy import contains\n\ndef handle(data):\n    return contains(data) if data["op"] == "contains" else get_value(data)\n'}
        reference_files = {**files,
            'core.py':'def lookup(records, *, key, default=None):\n    return {"found": key in records, "value": records.get(key, default)}\n',
            'consumer.py':'from core import lookup\n\ndef get_value(data):\n    return lookup(data["records"], key=data["key"], default=data.get("default"))["value"]\n',
            'legacy.py':'from core import lookup\n\ndef contains(data):\n    return lookup(data["records"], key=data["key"])["found"]\n'}
    namespace={}
    exec(function(reference), namespace)
    def cases(inputs):
        return [{'input':copy.deepcopy(x),'expected':namespace['solve'](copy.deepcopy(x))} for x in inputs]
    return {'key':key, 'issue':issue+' All editable files must remain importable Python. Do not mutate request inputs.',
            'files':files, 'public':cases(public), 'acceptance':cases(public+hidden),
            'reference_files':reference_files}


def corpus_identity():
    return digest([fixture(i) for i in range(len(ISSUES))])
