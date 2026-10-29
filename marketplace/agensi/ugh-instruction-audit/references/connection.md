# External service connection

There are two separate connections:

1. The user's agent signs in to Agensi to load this skill. The user does not need
   to download or install the creator's ZIP.
2. The agent separately connects to an operator-run UGH Audit MCP endpoint over
   HTTPS with a per-user Bearer invite token and the user's own Anthropic API key.
   Agensi does not host this endpoint, issue either credential, or supply model
   credits. Model use is billed to the user's Anthropic account; the operator pays
   hosting costs. The supported BYOK provider is Anthropic.

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

This implementation provides invite-token authentication. It does not implement
an OAuth authorization server. General ChatGPT connector OAuth compatibility has
not been established. Agensi-connected client support for these custom headers
also requires an actual test; no compatibility claim is made here. Do not claim that importing this template connects every
agent, or direct a user to disable authentication to make a connector work.

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
