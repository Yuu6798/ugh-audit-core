#!/usr/bin/env bash
# Compatibility entry point for the existing SessionEnd hook.
# Windows without bash: python scripts/doc_consistency_check.py
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$ROOT/scripts/doc_consistency_check.py" "$@"
fi
exec python "$ROOT/scripts/doc_consistency_check.py" "$@"
