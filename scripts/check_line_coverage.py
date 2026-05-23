#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce line coverage from coverage.py XML.")
    parser.add_argument("--coverage", type=Path, required=True, help="Path to coverage XML")
    parser.add_argument("--min", type=float, required=True, help="Minimum line coverage percent")
    parser.add_argument("--label", default="coverage", help="Label for output")
    args = parser.parse_args()

    root = ET.parse(args.coverage).getroot()
    covered = int(root.attrib["lines-covered"])
    valid = int(root.attrib["lines-valid"])
    percent = covered / valid * 100
    missing = max(0, math.ceil(args.min / 100 * valid) - covered)

    print(f"{args.label} line coverage: {covered}/{valid} = {percent:.2f}%")
    if percent + 1e-9 < args.min:
        print(
            f"{args.label} line coverage gate failed: {percent:.2f}% < {args.min:.2f}% "
            f"({missing} more covered lines needed)."
        )
        return 1

    print(f"{args.label} line coverage gate passed: {percent:.2f}% >= {args.min:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
