# External service connection

There are two separate connections:

1. The user's agent signs in to Agensi with OAuth to load this skill after the
   listing is live. The user does not need to download or install the creator's ZIP.
2. The agent separately connects to an operator-run UGH Audit MCP endpoint over
   HTTPS with a per-user Bearer invite token and the user's own Anthropic API key.
   Agensi does not host this endpoint, issue either credential, or supply model
   credits. Model use is billed to the user's Anthropic account; the operator pays
   hosting costs. The supported BYOK provider is Anthropic.

Agensi OAuth authorizes the Agensi connection only; it does not authenticate to
UGH or supply its invitation token or provider key. Agensi currently permits one
agent connection per account: signing in from a new client revokes the previous
client's access. Check which client you intend to use before switching.
[Agensi connection guide](https://www.agensi.io/docs/mcp-setup),
[OAuth and connection policy](https://www.agensi.io/docs/mcp-server).

Use `Authorization: Bearer <invite token>` for service identity and
`X-UGH-Anthropic-Key: <user's Anthropic API key>` for each audit. These are separate
credentials. History reads, MCP initialization and tool discovery need only the
invite token; every audit needs the provider key, including explicit requirements.

The endpoint in [mcp-config.json](../templates/mcp-config.json) is a nonworking
example. It is an illustrative client configuration, not a universal MCP config
schema. Use the actual host's supported Streamable HTTP settings. Some hosts use
different field names or cannot interpolate environment variables in headers.
The host must support both Authorization and custom secret HTTP headers. Enter
both credentials using that host's secret or connection settings; do not paste
them into chat, tool arguments, request JSON, audit text, a repository, a listing,
or an exported artifact. Do not ask the agent to inspect the secret values.

The `.env.example` names are client-side placeholders for a compatible host.
The audit server does not read `UGH_MCP_ENDPOINT`, `UGH_AUDIT_TOKEN` or
`UGH_ANTHROPIC_API_KEY` as server configuration. The template maps the last two
client placeholders to headers. A `.env` file is not automatically loaded by this
skill or server. Use host-supported secret storage/substitution; do not send a
literal interpolation marker as a credential or assume `${...}` works in every host.

## Codex CLI configuration example

Codex CLI is the selected client. Its native MCP client was checked with both
headers against a local controlled provider, and with invite-only reads and
missing-provider refusal against the deployed service (2026-10-29,
`codex-cli 0.165.0-alpha.1.2`). This was a direct MCP client check, without an agent
model turn, paid Anthropic call or Agensi skill load. This example uses native
`config.toml` fields rather than the illustrative JSON template:

```toml
[mcp_servers.ugh_audit]
url = "https://audit.example.com/mcp/"
bearer_token_env_var = "UGH_AUDIT_TOKEN"
env_http_headers = { "X-UGH-Anthropic-Key" = "UGH_ANTHROPIC_API_KEY" }
```

Configure the separate skill source with
`codex mcp add agensi --url https://mcp.agensi.io/mcp/auth`, then complete
`codex mcp login agensi` in the browser with your own Agensi account. This sign-in
can replace an existing Agensi client connection as described above.

Replace the nonworking example URL with the trusted operator's endpoint. Supply
the UGH invitation token and your own Anthropic key through the named environment
variables available to the Codex process, using your local secret-management
workflow. The values above are variable names, not credentials; do not put secret
values into the TOML. This maps the invitation to the Bearer header and the user
key to the separate provider header. `codex mcp login agensi` concerns the separate
Agensi OAuth connection; it does not sign in to UGH. The documented header fields
do not establish end-to-end compatibility by themselves.
[Official Codex CLI MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

## Compatibility and service use

This implementation provides invite-token authentication. It does not implement
an OAuth authorization server. General ChatGPT connector OAuth compatibility has
not been established. The complete Agensi skill-delivery and paid-audit workflow
still requires an actual test; the direct Codex check does not establish it. Do not claim
that importing this template connects every agent, or direct a user to disable
authentication to make a connector work.

Before publication, the creator can test the repository skill with their own
agent and validate the UGH connection. Agensi serves only live listings, so the
OAuth → `get_skill` → UGH audit workflow must be checked separately after the
listing goes live. A local skill or direct UGH test does not prove Agensi delivery.
[Agensi creator FAQ](https://www.agensi.io/docs/creator-faq).

If no supported configured connection exposes `audit_answer`, explain the missing
dependency and stop the service workflow. Do not install a plugin, configure a
server, deploy code, or make a paid model call merely because this skill loaded.
An authorized operator can deploy the separately licensed GitHub project; normal
skill users need a compatible agent, an existing service invitation and their own
Anthropic API account/key. Do not replace a missing user key with the operator's key.

Requests send the chosen question, answer and requirement metadata to the audit
operator. The provider key passes through that operator to Anthropic, so connect
only to a trusted endpoint whose data handling you accept. The application uses
the key for the request and does not save it to audit history or logs. Configure
clients and proxies not to log secret headers. Required semantic checks send each
requirement and answer to Anthropic. Optional requirement generation uses the
same user key and bypasses shared metadata caches in public invite mode.
Successful resolved audits may be saved in the operator's database. Confirm the
operator's retention and deletion policy before sending confidential material.
The package itself supplies no production privacy policy or hosted-service SLA.

An audit without the provider header is refused with `provider_key_required`
(REST HTTP 400). A server environment key is never a substitute in public invite
mode. Provider rejection or API failure must not be described as acceptance.
HTTP 401 includes missing, invalid, disabled or expired invite credentials. HTTP
403 can indicate a rejected MCP Origin. HTTP 429 is a usage limit; honor its
`Retry-After` guidance. HTTP 503 indicates unavailable configuration or usage
control. Do not reclassify any of these as a model finding about the answer.

Agensi distribution details: [what you can sell](https://www.agensi.io/docs/what-you-can-sell),
[delivery terms](https://www.agensi.io/terms). GitHub source remains independently
available under the bundled [MIT license](../LICENSE); no platform-term exception
is asserted for the copy delivered through Agensi.
