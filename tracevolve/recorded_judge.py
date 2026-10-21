"""Exact-request replay only. No model client or network dependency."""
import argparse
import json
import sys
from pathlib import Path

from .legacy import pilot
from .util import read_json


def lookup(bank, request):
    key = pilot.digest(request)
    entry = bank.get(key)
    if not entry or pilot.digest(entry["request"]) != key or entry["response"] is None:
        raise ValueError("unknown, corrupted or failed sealed judgment")
    return entry["response"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bank", required=True)
    args = p.parse_args()
    try:
        response = lookup(read_json(Path(args.bank)), json.load(sys.stdin))
        print(json.dumps(response, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, KeyError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
