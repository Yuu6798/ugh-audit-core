#!/usr/bin/env bash
# scripts/doc_consistency_check.sh
# SessionStart hook: detect doc/code inconsistencies and instruct Claude to fix them.
# Output is fed to Claude as context. No output = no issues.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
issues=()

# --- 1. Top-level .py files missing from CLAUDE.md Architecture tree ---
for py in "$ROOT"/*.py; do
    [ -f "$py" ] || continue
    base="$(basename "$py")"
    if ! grep -qF "$base" "$ROOT/CLAUDE.md"; then
        issues+=("CLAUDE.md Architecture tree missing: $base")
    fi
done

# --- 2. Top-level .py files missing from README.md directory tree ---
for py in "$ROOT"/*.py; do
    [ -f "$py" ] || continue
    base="$(basename "$py")"
    if ! grep -qF "$base" "$ROOT/README.md"; then
        issues+=("README.md directory tree missing: $base")
    fi
done

# --- 3. docs/*.md files missing from CLAUDE.md index table ---
for doc in "$ROOT"/docs/*.md; do
    [ -f "$doc" ] || continue
    base="$(basename "$doc")"
    # skip task specs and addendums (not permanent docs)
    case "$base" in
        *_task.md|*_addendum.md) continue ;;
    esac
    if ! grep -qF "$base" "$ROOT/CLAUDE.md"; then
        issues+=("CLAUDE.md doc index missing: docs/$base")
    fi
done

# --- 4. docs/*.md files missing from README.md ---
for doc in "$ROOT"/docs/*.md; do
    [ -f "$doc" ] || continue
    base="$(basename "$doc")"
    case "$base" in
        *_task.md|*_addendum.md) continue ;;
    esac
    if ! grep -qF "$base" "$ROOT/README.md"; then
        issues+=("README.md missing reference to: docs/$base")
    fi
done

# --- 5. README.md outdated markers ---
if grep -q 'grv.*未着手\|grv.*未実装' "$ROOT/README.md"; then
    issues+=("README.md: grv is marked as unimplemented but grv_calculator.py exists (v1.4)")
fi

if ! grep -q 'mode_affordance\|mode_signal\|response_mode_signal' "$ROOT/README.md"; then
    issues+=("README.md: no mention of mode_affordance / response_mode_signal")
fi

if ! grep -q 'semantic_loss\|L_sem' "$ROOT/README.md" 2>/dev/null; then
    # L_sem is already mentioned, skip if found
    :
fi

if ! grep -q 'grv_calculator' "$ROOT/README.md"; then
    issues+=("README.md: grv_calculator.py not in directory structure")
fi

if ! grep -q 'fallback\|computed_ai_draft' "$ROOT/README.md"; then
    issues+=("README.md: metadata_source table missing fallback/computed_ai_draft")
fi

# --- 6. Key thresholds: spot-check a few constants ---
# Check grv weights in CLAUDE.md match grv_calculator.py
if [ -f "$ROOT/grv_calculator.py" ]; then
    code_wd=$(grep -oP 'W_DRIFT\s*=\s*\K[0-9.]+' "$ROOT/grv_calculator.py" 2>/dev/null || echo "")
    if [ -n "$code_wd" ] && ! grep -qF "$code_wd" "$ROOT/CLAUDE.md"; then
        issues+=("CLAUDE.md: grv W_DRIFT=$code_wd not found in Key Thresholds")
    fi
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
echo "Please fix these inconsistencies:"
echo "1. Update CLAUDE.md Architecture tree and doc index table"
echo "2. Update README.md directory structure, feature descriptions, and metadata_source table"
echo "3. Commit changes to the current branch"
echo "4. Follow CLAUDE.md doc management policy (minimal additions, details in docs/)"
