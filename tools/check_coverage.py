"""Enforce statement and branch coverage independently."""

import json
import sys
from pathlib import Path


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "coverage.json")
    totals = json.loads(path.read_text(encoding="utf-8"))["totals"]
    statements = 100 * totals["covered_lines"] / totals["num_statements"]
    branches = 100 * totals["covered_branches"] / totals["num_branches"] if totals["num_branches"] else 100
    print(f"Statements: {statements:.2f}%; branches: {branches:.2f}%; required: 95% each")
    return 0 if min(statements, branches) >= 95 else 1


if __name__ == "__main__":
    raise SystemExit(main())
