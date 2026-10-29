# External service connection

There are two separate connections:

1. The user's agent signs in to Agensi to load this skill. The user does not need
   to download or install the creator's ZIP.
2. The agent separately connects to an operator-run UGH Audit MCP endpoint over
   HTTPS with a per-user Bearer invite token. Agensi does not host this endpoint,
   issue this token, or pay the operator's model and hosting costs.

The endpoint in [mcp-config.json](../templates/mcp-config.json) is a nonworking
example. It is an illustrative client configuration, not a universal MCP config
schema. Use the actual host's supported Streamable HTTP settings. Some hosts use
different field names or cannot interpolate environment variables in headers.
Enter the real token using that host's secret or connection settings; do not
paste it into chat, the audit text, a repository, a listing, or a public artifact.

The `.env.example` names are client-side placeholders for a compatible host.
The audit server does not read `UGH_MCP_ENDPOINT` or `UGH_AUDIT_TOKEN` itself.
Do not send the literal interpolation marker as a credential.

This implementation provides invite-token authentication. It does not implement
an OAuth authorization server. General ChatGPT connector OAuth compatibility has
not been established. Do not claim that importing this template connects every
agent, or direct a user to disable authentication to make a connector work.

If no supported configured connection exposes `audit_answer`, explain the missing
dependency and stop the service workflow. Do not install a plugin, configure a
server, deploy code, or make a paid model call merely because this skill loaded.
An authorized operator can deploy the separately licensed GitHub project; normal
skill users only need a compatible agent and an existing service invitation.

Requests send the chosen question, answer and requirement metadata to the audit
operator. Required semantic checks send each requirement and answer to Anthropic.
Successful resolved audits may be saved in the operator's database. Confirm the
operator's retention and deletion policy before sending confidential material.
The package itself supplies no production privacy policy or hosted-service SLA.

HTTP 401 includes missing, invalid, disabled or expired invite credentials. HTTP
403 can indicate a rejected MCP Origin. HTTP 429 is a usage limit; honor its
`Retry-After` guidance. HTTP 503 indicates unavailable configuration or usage
control. Do not reclassify any of these as a model finding about the answer.

Agensi distribution details: [what you can sell](https://www.agensi.io/docs/what-you-can-sell),
[delivery terms](https://www.agensi.io/terms). GitHub source remains independently
available under the bundled [MIT license](../LICENSE); no platform-term exception
is asserted for the copy delivered through Agensi.
