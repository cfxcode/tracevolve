"""Run untrusted task artifacts with OS isolation; expected values stay in host."""
from __future__ import annotations
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tempfile
from .util import canonical

WORKER = '''import sys,json,copy,importlib,contextlib,io
sys.path.insert(0,sys.argv[1])
request=json.loads(sys.stdin.read())
answers=[]
with contextlib.redirect_stdout(io.StringIO()):
    module=importlib.import_module(request['entrypoint'])
    for item in request['inputs']:
        original=copy.deepcopy(item)
        try:
            value=module.handle(item)
            answers.append({'value':value,'input_unchanged':json.dumps(item,sort_keys=True,allow_nan=False)==json.dumps(original,sort_keys=True,allow_nan=False)})
        except Exception as exc:
            answers.append({'exception':type(exc).__name__})
print(json.dumps(answers,ensure_ascii=False,allow_nan=False))
'''


def available():
    return sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').is_file()


def profile(root):
    # Literal / permits dyld root lookup, not recursive host access. No fork,
    # network, user files, credentials, study files or arbitrary writes allowed.
    return ('(version 1)(deny default)(allow process-exec)(allow file-read-metadata)'
            '(allow file-read* (literal "/") (subpath "/System") (subpath "/usr/lib") '
            '(subpath '+json.dumps(str(Path(sys.base_prefix).resolve()))+') '
            '(subpath "/dev") (subpath '+json.dumps(str(root))+'))'
            '(allow file-write* (subpath '+json.dumps(str(root))+'))'
            '(allow sysctl-read)(allow mach-lookup)')


def _limits():
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024*1024, 1024*1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    # macOS does not reliably enforce RLIMIT_AS. Bound resident growth using
    # subprocess deadline and deny forks; never advertise a hard memory cap.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def execute(files, entrypoint, inputs):
    if not available():
        raise RuntimeError('CODE_SANDBOX_UNAVAILABLE: macOS Seatbelt is required; no unsandboxed fallback')
    if (not isinstance(files,dict) or not files or len(files)>16 or
        any(not isinstance(k,str) or not k.endswith('.py') or '/' in k or '\\' in k or k.startswith('.')
            or not isinstance(v,str) or len(v.encode())>32768 for k,v in files.items())):
        raise ValueError('invalid code artifact')
    with tempfile.TemporaryDirectory(prefix='tracevolve-code-') as directory:
        root=Path(directory).resolve()
        for name,content in files.items(): (root/name).write_text(content)
        with (root/'stdout').open('w+') as out, (root/'stderr').open('w+') as err:
            proc=subprocess.Popen(['/usr/bin/sandbox-exec','-p',profile(root),sys.executable,
                '-I','-S','-B','-c',WORKER,str(root)],stdin=subprocess.PIPE,stdout=out,stderr=err,
                text=True,cwd=root,env={'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8','TMPDIR':str(root)},
                start_new_session=True,preexec_fn=_limits)
            timeout=False
            try:
                proc.communicate(canonical({'entrypoint':entrypoint,'inputs':inputs}),timeout=8)
            except subprocess.TimeoutExpired:
                timeout=True
            finally:
                try: os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                proc.wait()
            out.seek(0); raw=out.read(1048577)
            # Never expose arbitrary stderr: it may contain nondeterministic paths
            # or task-controlled content that is not a structured test result.
            if timeout: return {'error':'TEST_TIMEOUT'}
            if proc.returncode: return {'error':'TEST_PROCESS_FAILED'}
            try:
                answers=json.loads(raw)
                if not isinstance(answers,list) or len(answers)!=len(inputs): raise ValueError()
                if any(not isinstance(a,dict) or (set(a)!={'value','input_unchanged'} and set(a)!={'exception'}) for a in answers): raise ValueError()
                return {'answers':answers}
            except (ValueError,TypeError): return {'error':'INVALID_TEST_OUTPUT'}


def check(files, entrypoint, cases):
    result=execute(files,entrypoint,[c['input'] for c in cases])
    if 'error' in result:
        return {'passed':0,'total':len(cases),'failure':result['error']}
    passed=[a.get('input_unchanged') is True and 'value' in a and
            canonical(a['value'])==canonical(c['expected']) for a,c in zip(result['answers'],cases)]
    return {'passed':sum(passed),'total':len(cases),'checks':passed}
