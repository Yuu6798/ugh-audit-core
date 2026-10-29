# Build the creator upload package

The upload is a single skill. Buyers use it through an Agensi-connected agent;
they do not install this ZIP. The external MCP server is operated separately.
The listing draft and withdrawal notes beside the skill directory are creator
working materials. The skill ZIP includes client connection templates and operator
setup references; operators read the latter only when setup is actually requested.
Free distribution requires an external invitation, the user's own Anthropic API
key (BYOK), and a client supporting both secret headers. The ZIP contains empty
client placeholders and environment references only; it contains no usable key,
model credit, working endpoint or proof of Agensi-client compatibility.

From the repository root, select a new output path explicitly:

```console
python scripts/build_agensi_package.py --output /path/to/ugh-instruction-audit.zip
```

The builder uses an explicit allowlist, UTF-8 text, fixed ZIP metadata and stored
entries for byte-for-byte repeatability. It refuses overwrite, symlinks, malformed
templates, unexpected environment assignments, common credential patterns and
limit violations. It copies the exact repository MIT LICENSE and checks the skill
copy matches. It never reads a real environment file, token registry, audit/usage
database, runtime key, or model cache. A small scanner cannot prove arbitrary text
is secret-free; review the allowlisted files before submission.

The ZIP contains only:

```text
SKILL.md
.env.example
LICENSE
references/audit-workflow.md
references/connection.md
references/operator-setup.md
templates/mcp-config.json
templates/operator.env.example
```

There is one SKILL.md and no bundled executable, server implementation, dependency,
virtual environment, buyer-session script, or nested ZIP. The build script is a
creator-side repository tool and is not shipped to the buyer.

The builder enforces the stricter agent-upload budget of 100 files and 5,000,000
bytes, plus a 20,000-byte limit for any packaged script. The present package uses
eight text files. Agensi still performs its own scan and review; local checks do not
guarantee approval. [Official packaging guide](https://www.agensi.io/docs/packaging)

Before publication, complete the external-service checks in
[operator-setup.md](operator-setup.md), resolve the nonworking endpoint placeholder
in the actual connection setup, and review [listing-draft.md](listing-draft.md) and
[rights-and-withdrawal.md](rights-and-withdrawal.md). This build does not submit
anything to Agensi or start a deployment.

The packaged connection and operator guides link to the repository's
[data handling and deletion policy](../../docs/data-policy.md). The creator-operated
beta contact is [kkoo6798@gmail.com](mailto:kkoo6798@gmail.com). Verify actual receipt
and the operating prerequisites separately from choosing this address, and complete the
[release checklist](../../docs/beta-release-checklist.md), before submission.
These repository documents are maintained outside the eight-file ZIP; check that
their links resolve on the reviewed, merged revision before uploading. A ZIP build
does not activate retention, accept a deletion request, or lift the publication hold.

Validation has two stages. Before publication, test the repository skill with
the creator's own agent and check its UGH invitation and BYOK headers. Codex CLI
is the selected client; direct MCP transport checks passed, while Agensi skill
delivery and a real paid model audit remain unverified. Its native configuration
example is in [connection.md](ugh-instruction-audit/references/connection.md).
Agensi serves a listing only after it is live, so the subsequent Agensi OAuth →
`get_skill` → UGH audit check is a separate post-publication step. Neither a ZIP
build nor a direct UGH connection proves that delivery path.
[Agensi creator FAQ](https://www.agensi.io/docs/creator-faq).
