#!/usr/bin/env sh
# Run every quality and security check. Usage:  sh scripts/check.sh
# Needs uv (https://docs.astral.sh/uv/). On Windows, run the same commands in PowerShell.
set -eu

echo "== ruff (lint + format check)"
uv run ruff check .
uv run ruff format --check .

echo "== tests"
uv run pytest -q

echo "== bandit (security linter)"
uv run bandit -r src -q

echo "== pip-audit (known vulnerabilities in locked production dependencies)"
uv export -q --frozen --no-dev --no-emit-project --no-hashes -o "${TMPDIR:-/tmp}/job-search-reqs.txt"
uv run pip-audit -r "${TMPDIR:-/tmp}/job-search-reqs.txt" --no-deps --disable-pip

echo "== detect-secrets (must print 0)"
uv run detect-secrets scan --all-files \
  --exclude-files '(^|/)(\.venv|\.git|\.cache|\.ruff_cache|\.pytest_cache|uv\.lock)(/|$)' \
  | python3 -c "import json,sys; d=json.load(sys.stdin)['results']; n=sum(len(v) for v in d.values()); print(n); sys.exit(1 if n else 0)"

echo "== lockfile is in sync with pyproject.toml"
uv lock --check

echo "ALL CHECKS PASSED"
