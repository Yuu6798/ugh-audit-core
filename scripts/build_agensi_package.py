"""Build the allowlisted, text-only creator upload for Agensi; never publish it."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import zipfile


FILES = (
    ".env.example",
    "LICENSE",
    "SKILL.md",
    "references/audit-workflow.md",
    "references/connection.md",
    "references/operator-setup.md",
    "templates/mcp-config.json",
    "templates/operator.env.example",
)
MAX_BYTES = 5_000_000
MAX_FILES = 100
MAX_SCRIPT_BYTES = 20_000
SECRET_PATTERNS = (
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"\bsk-(?:ant-[A-Za-z0-9_-]+|proj-[A-Za-z0-9_-]+|[A-Za-z0-9]{24,})",
    r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}",
    r"\bBearer [A-Za-z0-9_-]{32,256}\b",
)


def package_bytes(repository: Path) -> tuple[bytes, dict]:
    """Read only the allowlist and return deterministic ZIP bytes and an inventory."""
    source = repository / "marketplace" / "agensi" / "ugh-instruction-audit"
    if source.is_symlink():
        raise ValueError("The skill directory must not be a symlink")
    root = source.resolve(strict=True)
    entries = {}
    for relative in FILES:
        path = source / relative
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError(f"Symlink not allowed: {relative}")
        if not path.resolve(strict=True).is_relative_to(root) or not path.is_file():
            raise ValueError(f"Invalid package path: {relative}")
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        if "\x00" in text or any(re.search(pattern, text) for pattern in SECRET_PATTERNS):
            raise ValueError(f"Binary content or possible credential in {relative}")
        if path.suffix in (".py", ".sh", ".js", ".ps1") and len(raw) >= MAX_SCRIPT_BYTES:
            raise ValueError(f"Packaged script exceeds the 20 KB budget: {relative}")
        entries[relative] = raw

    skill = entries["SKILL.md"].decode("utf-8")
    frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", skill, re.DOTALL)
    if not frontmatter:
        raise ValueError("SKILL.md needs YAML frontmatter")
    for field in ("name", "description"):
        if not re.search(rf"(?m)^{field}:\s*\S.+$", frontmatter.group(1)):
            raise ValueError(f"SKILL.md needs {field}")
    if sum(Path(name).name == "SKILL.md" for name in entries) != 1:
        raise ValueError("Exactly one SKILL.md is required")
    if entries["LICENSE"] != (repository / "LICENSE").read_bytes():
        raise ValueError("The packaged MIT LICENSE must match the repository LICENSE")

    env_lines = entries[".env.example"].decode("utf-8").splitlines()
    assignments = [line for line in env_lines if line and not line.startswith("#")]
    if assignments != ["UGH_MCP_ENDPOINT=https://audit.example.com/mcp/", "UGH_AUDIT_TOKEN="]:
        raise ValueError("Client .env.example must contain only the empty-token example")
    config = json.loads(entries["templates/mcp-config.json"])
    expected = {"mcpServers": {"ugh-instruction-audit": {
        "type": "http", "url": "https://audit.example.com/mcp/",
        "headers": {"Authorization": "Bearer ${UGH_AUDIT_TOKEN}"},
    }}}
    if config != expected:
        raise ValueError("MCP template must retain the nonworking endpoint and token placeholder")
    operator = entries["templates/operator.env.example"].decode("utf-8").splitlines()
    expected_operator = [
        "UGH_ACCESS_MODE=invite", "UGH_INVITE_FILE=/data/ugh/invites.json",
        "UGH_AUDIT_DB=/data/ugh/audit.db", "UGH_USAGE_DB=/data/ugh/usage.db",
        "UGH_AUDIT_CACHE_DIR=/data/ugh/cache", "UGH_META_CACHE_DIR=/data/ugh/meta-cache",
        "UGH_ALLOWED_HOSTS=audit.example.com", "UGH_ALLOWED_ORIGINS=",
        "UGH_REQUESTS_PER_MINUTE=60", "UGH_AUDITS_PER_DAY=100",
        "UGH_GLOBAL_AUDITS_PER_DAY=500", "UGH_CONCURRENT_PER_USER=1",
        "UGH_CONCURRENT_GLOBAL=4", "ANTHROPIC_API_KEY=",
    ]
    if [line for line in operator if line and not line.startswith("#")] != expected_operator:
        raise ValueError("Operator environment must retain only the documented non-secret defaults")
    if len(entries) > MAX_FILES or sum(map(len, entries.values())) > MAX_BYTES:
        raise ValueError("Package exceeds the agent-upload size or file budget")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, entries[name])
    content = output.getvalue()
    if len(content) > MAX_BYTES:
        raise ValueError("ZIP exceeds the agent-upload size budget")
    return content, {
        "files": sorted(entries), "file_count": len(entries), "zip_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(), "published": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new creator-upload ZIP path")
    args = parser.parse_args()
    try:
        content, inventory = package_bytes(Path(__file__).resolve().parent.parent)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as output:
            output.write(content)
    except (OSError, ValueError, UnicodeError) as exc:
        parser.exit(1, f"Build failed: {exc}\n")
    print(json.dumps({"output": str(args.output.resolve()), **inventory}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
