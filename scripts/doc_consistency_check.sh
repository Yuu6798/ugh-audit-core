#!/usr/bin/env bash
# scripts/doc_consistency_check.sh
# SessionStart hook: detect README.md vs code/docs inconsistencies.
# Output is fed to Claude as context. No output = no issues.
# Scope: README.md only (CLAUDE.md is manually maintained under 400-line limit).

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
issues=()

# --- 1. Top-level .py files missing from README.md directory tree ---
for py in "$ROOT"/*.py; do
    [ -f "$py" ] || continue
    base="$(basename "$py")"
    if ! grep -qF "$base" "$ROOT/README.md"; then
        issues+=("README.md directory tree missing: $base")
    fi
done

# --- 2. docs/*.md files missing from README.md ---
for doc in "$ROOT"/docs/*.md; do
    [ -f "$doc" ] || continue
    base="$(basename "$doc")"
    # skip task specs and addendums (not permanent docs)
    case "$base" in
        *_task.md|*_addendum.md) continue ;;
    esac
    if ! grep -qF "$base" "$ROOT/README.md"; then
        issues+=("README.md missing reference to: docs/$base")
    fi
done

# --- 3. README.md outdated markers ---
if grep -q 'grv.*未着手\|grv.*未実装' "$ROOT/README.md"; then
    issues+=("README.md: grv is marked as unimplemented but grv_calculator.py exists (v1.4)")
fi

if ! grep -q 'mode_affordance\|mode_signal\|response_mode_signal' "$ROOT/README.md"; then
    issues+=("README.md: no mention of mode_affordance / response_mode_signal")
fi

if ! grep -q 'grv_calculator' "$ROOT/README.md"; then
    issues+=("README.md: grv_calculator.py not in directory structure")
fi

if ! grep -q 'fallback\|computed_ai_draft' "$ROOT/README.md"; then
    issues+=("README.md: metadata_source table missing fallback/computed_ai_draft")
fi

# --- Output ---
if [ ${#issues[@]} -eq 0 ]; then
    exit 0
fi

echo "=== Doc Consistency Check: ${#issues[@]} issue(s) found ==="
echo ""
for issue in "${issues[@]}"; do
    echo "- $issue"
done
echo ""
echo "Please fix these inconsistencies in README.md:"
echo "1. Update directory structure section"
echo "2. Update design docs index table"
echo "3. Fix outdated feature descriptions"
echo "4. Commit changes to the current branch"
