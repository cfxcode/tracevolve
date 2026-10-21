import hashlib
import json
import unittest
from pathlib import Path


class VendorIntegrityTests(unittest.TestCase):
    def test_runtime_and_original_tests_use_identical_frozen_sources(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "vendor/provenance.json").read_text())
        for name, expected in manifest["files"].items():
            actual = hashlib.sha256((root / "vendor/agent_validation_pilot" / name).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, name)
        for name in manifest["runtime_copies"]:
            actual = hashlib.sha256((root / "tracevolve/_vendor" / name).read_bytes()).hexdigest()
            self.assertEqual(actual, manifest["files"][name], name)
