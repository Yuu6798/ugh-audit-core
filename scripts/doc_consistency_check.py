"""Check README entry points and linked API documentation without importing the app.

No output means no issues. The default is advisory for the SessionEnd hook;
--strict returns a failing status for CI. Run directly with Python on Windows.
This is a static documentation check, not verification of runtime semantics.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re


def check_docs(root: Path) -> list[str]:
    """Return documentation inconsistencies relative to the repository root."""
    issues: list[str] = []

    def read(relative: str) -> str:
        try:
            return (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            issues.append(f"{relative}: cannot read ({type(exc).__name__})")
            return ""

    readme = read("README.md")
    # Only inline local Markdown links, the format used by the README index.
    links = set(re.findall(r"\]\((?:\./)?(docs/[^\s)#]+\.md)(?:#[^\s)]+)?\)", readme))

    for path in sorted(root.glob("*.py")):
        if path.is_file() and path.name not in readme:
            issues.append(f"README.md directory tree missing: {path.name}")

    doc_paths = {
        f"docs/{path.name}" for path in (root / "docs").glob("*.md")
        if path.is_file() and not path.name.endswith(("_task.md", "_addendum.md"))
    }
    api_path = "docs/server_api.md"
    for relative in sorted(doc_paths | {api_path}):
        if relative not in links:
            issues.append(f"README.md missing link to: {relative}")

    for relative in sorted(links):
        if not (root / relative).is_file():
            issues.append(f"README.md broken link: {relative}")

    if re.search(r"grv.*(?:未着手|未実装)", readme):
        issues.append("README.md: grv is marked as unimplemented but grv_calculator.py exists (v1.4)")
    if not re.search(r"mode_affordance|mode_signal|response_mode_signal", readme):
        issues.append("README.md: no mention of mode_affordance / response_mode_signal")
    if "grv_calculator" not in readme:
        issues.append("README.md: grv_calculator.py not in directory structure")

    # README remains an entry point. Field details belong in the linked API spec.
    api = read(api_path)
    for marker, field in (
        ("`fallback`", "metadata_source"),
        ("`metadata_fallback_unverified`", "degraded_reason"),
        ("`computed_ai_draft`", "mode"),
    ):
        if marker not in api:
            issues.append(f"{api_path}: {field} documentation missing {marker}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="exit 1 if inconsistencies are found")
    args = parser.parse_args()
    issues = check_docs(Path(__file__).resolve().parent.parent)
    if issues:
        print(f"=== Doc Consistency Check: {len(issues)} issue(s) found ===")
        for issue in issues:
            print(f"- {issue}")
        print("Fix the reported file or README link; keep field details in docs/.")
    return 1 if issues and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
