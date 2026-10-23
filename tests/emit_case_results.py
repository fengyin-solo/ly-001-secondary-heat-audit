#!/usr/bin/env python3
"""Append behavior-level outcomes to the JUnit-derived verifier log."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", required=True, type=Path)
    parser.add_argument("--case-map", required=True, type=Path)
    args = parser.parse_args()

    mapping = json.loads(args.case_map.read_text(encoding="utf-8"))
    root = ET.parse(args.junit).getroot()
    outcomes: dict[str, str] = {}
    for testcase in root.iter("testcase"):
        name = testcase.get("name", "")
        if testcase.find("failure") is not None:
            outcomes[name] = "FAILED"
        elif testcase.find("error") is not None:
            outcomes[name] = "ERROR"
        elif testcase.find("skipped") is not None:
            outcomes[name] = "SKIPPED"
        else:
            outcomes[name] = "PASSED"

    for test_name, behaviors in mapping.items():
        status = outcomes.get(test_name, "MISSING")
        for behavior in behaviors:
            print(f"{status} {behavior['token']} {behavior['behavior_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
