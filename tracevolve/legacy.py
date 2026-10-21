"""Load the untouched v0.2.1 scripts in their original import layout."""
import importlib
import sys
from pathlib import Path

_path = str(Path(__file__).parent / "_vendor")
if _path not in sys.path:
    sys.path.insert(0, _path)
contracts = importlib.import_module("contracts")
probe = importlib.import_module("rename_probe")
pilot = importlib.import_module("pilot")
