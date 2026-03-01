#!/usr/bin/env python3
"""Run pattern matching against sample_pane.txt and report results."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.quick_select import BUILTIN_PATTERNS, find_matches

SAMPLE = os.path.join(os.path.dirname(__file__), "sample_pane.txt")


def main():
    with open(SAMPLE) as f:
        lines = f.read().splitlines()

    patterns = list(BUILTIN_PATTERNS.values())
    unique, all_pos = find_matches(lines, patterns, height=len(lines))

    print(f"Lines in sample: {len(lines)}")
    print(f"Unique matches:  {len(unique)}")
    print(f"Total occurrences: {sum(len(v) for v in all_pos.values())}")
    print()

    # Group by which pattern matched
    import re
    for name, pat in BUILTIN_PATTERNS.items():
        regex = re.compile(pat)
        hits = set()
        for line in lines:
            for m in regex.finditer(line):
                hits.add(m.group(0))
        if hits:
            print(f"[{name}] {len(hits)} unique:")
            for h in sorted(hits):
                print(f"  {h}")
            print()

    print("--- All unique matches (bottom-to-top label order) ---")
    for i, (row, col, text) in enumerate(unique):
        print(f"  {i:2d}. row={row:3d} col={col:3d}  {text!r}")


if __name__ == "__main__":
    main()
