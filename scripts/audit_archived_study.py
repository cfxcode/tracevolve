"""Replay a study using its hash-checked source archive, without model access."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tracevolve.store import Store
from tracevolve.util import text_hash


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--study',required=True)
    parser.add_argument('--summary-output')
    args=parser.parse_args()
    root=Path(args.study).resolve()
    if not (root/'state.sqlite').is_file(): raise ValueError('study does not exist')
    store=Store(root)
    try:
        store.verify()
        frozen=store.get(store.state('frozen'))
        files=store.get(store.state('source_archive'))['files']
        expected=frozen['identity']['source']
        if {name:text_hash(content) for name,content in files.items()}!=expected:
            raise ValueError('archived source differs from frozen source')
    finally: store.close()
    with tempfile.TemporaryDirectory(prefix='tracevolve-audit-') as directory:
        temporary=Path(directory)
        package=(temporary/'tracevolve').resolve()
        for name,content in files.items():
            destination=(package/name).resolve()
            if not destination.is_relative_to(package) or destination.suffix!='.py':
                raise ValueError('invalid archive path')
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_text(content)
        script=temporary/'scripts'/'audit_resource_runs.py'
        script.parent.mkdir()
        script.write_text(Path(__file__).with_name('audit_resource_runs.py').read_text())
        if args.summary_output:
            summary=script.with_name('summarize_studies.py')
            summary.write_text(Path(__file__).with_name('summarize_studies.py').read_text())
            return subprocess.run([sys.executable,str(summary),'--study',str(root),
                                   '--output',str(Path(args.summary_output).resolve())],cwd=temporary).returncode
        return subprocess.run([sys.executable,str(script),'--study',str(root)],cwd=temporary).returncode


if __name__=='__main__':
    raise SystemExit(main())
