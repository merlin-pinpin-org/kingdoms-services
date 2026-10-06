#!/usr/bin/env bash
# Guard: every pin-state client_payload stays under the GitHub API cap.
#
# A repository_dispatch client_payload accepts AT MOST 10 properties — a
# payload with 11 fails with a 422 "No more than 10 properties are
# allowed" at dispatch time (seen on 2026-09-24). All three emitters
# (deploy.yml, docker.yml, release.yml) currently send exactly 10, so
# the FIRST property anyone adds silently breaks a dispatch. This check
# counts the properties of every client_payload in the workflows and
# fails when one exceeds the cap, with the offending file named.
#
# Usage:
#   scripts/check_pin_payloads.sh          (from the repo root)
#
# Run by make check and CI (Lint workflows job).

set -euo pipefail

python3 - "$(pwd)" <<'PYEOF'
import re
import sys
from pathlib import Path

CAP = 10
root = Path(sys.argv[1])
failed = False
found = False

for workflow in sorted((root / ".github" / "workflows").glob("*.yml")):
    text = workflow.read_text()
    for match in re.finditer(r"client_payload:\s*\{", text):
        found = True
        start = match.end()  # just past the opening brace
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[start : i - 1]
        keys = []
        indent = None
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            if indent is None:
                indent = len(line) - len(line.lstrip())
            # top-level keys sit at the payload's own indentation
            if len(line) - len(line.lstrip()) == indent:
                key = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:,]", stripped)
                if key:
                    keys.append(key.group(1))
        count = len(keys)
        if count > CAP:
            print(
                f"::error file={workflow}::client_payload has {count} properties "
                f"({', '.join(keys)}) — the GitHub API caps a dispatch payload at "
                f"{CAP}; compose the extra fields in the Pin state workflow instead",
                file=sys.stderr,
            )
            failed = True
        else:
            print(f"{workflow}: client_payload with {count} properties (cap {CAP}) — OK")

if not found:
    print("::error::no client_payload found in any workflow — check the script's parser", file=sys.stderr)
    sys.exit(1)
if failed:
    sys.exit(1)
print("PIN PAYLOAD CHECK OK")
PYEOF
