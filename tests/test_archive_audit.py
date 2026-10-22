import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from tracevolve.config import Config
from tracevolve.store import Store
from tracevolve.study import Study
from test_core import SyntheticAdapter

SCRIPT=Path(__file__).resolve().parents[1]/'scripts'/'audit_archived_study.py'


class ArchiveAuditTests(unittest.TestCase):
    def test_frozen_source_audit_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory)
            try:
                Study(store,SyntheticAdapter(),Config(max_development_tasks=1)).freeze()
                result=subprocess.run([sys.executable,str(SCRIPT),'--study',directory],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('"verified": true',result.stdout)
                summary=Path(directory)/'summary.json'
                result=subprocess.run([sys.executable,str(SCRIPT),'--study',directory,'--summary-output',str(summary)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertTrue(summary.is_file())
                archive=store.get(store.state('source_archive'))
                archive['files']['tasks.py']+='\n# changed\n'
                store.set('source_archive',store.put(archive))
                result=subprocess.run([sys.executable,str(SCRIPT),'--study',directory],capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('archived source differs',result.stderr)
                self.assertEqual(store.calls(),[])
            finally: store.close()

    def test_missing_study_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            missing=Path(directory)/'missing'
            result=subprocess.run([sys.executable,str(SCRIPT),'--study',str(missing)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse(missing.exists())
