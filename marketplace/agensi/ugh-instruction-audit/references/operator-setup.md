# Operator setup for the invitation pilot

This is an operator guide included with the skill's text references. Preparing
the ZIP does not deploy a service or publish a listing. A real external HTTPS
endpoint, private invitations, retention policy and operating budget are still
needed. Agensi does not run this server.

## Install the reviewed implementation

Use Python 3.10+ and the exact reviewed commit from the invitation-beta PR, not an
old package bearing version `0.3.0`. Replace `APPROVED_INVITE_BETA_COMMIT` below
with that PR's full immutable commit SHA once review is complete. It must contain
`ugh_audit/beta_access.py`, `usage_limits.py` and `invites.py`.

```console
git clone https://github.com/Yuu6798/ugh-audit-core.git
cd ugh-audit-core
git checkout --detach APPROVED_INVITE_BETA_COMMIT
python -m venv .venv
```

Activate `.venv` using the operating system's normal command, then install the
actual server extra declared in that checkout's `pyproject.toml`:

```console
python -m pip install -e ".[server]"
python -m ugh_audit.invites --help
```

This extra includes FastAPI, Uvicorn, `mcp>=1.30,<2`, sentence-transformers and Anthropic
SDK support. No OpenAI key is needed for this workflow. Do not install a guessed
package name or treat the marketplace skill as the server executable.

## Configure private persistent state

Use [operator.env.example](../templates/operator.env.example) as the variable checklist.
Export it through the actual deployment environment; the application does not
automatically load this example.
Keep `UGH_ACCESS_MODE=invite`. `local` is a trusted local/admin mode with unscoped
history and is unsuitable for an externally reachable invitation service.

`UGH_INVITE_FILE` points to a private JSON registry containing stable user IDs and
SHA256 token hashes, not plaintext tokens. Create one identity per invited person.
The operator CLI writes a newly issued token to an explicitly chosen private file:

POSIX deployment example, run as the service's operating-system user. Create the
parent directories first; the CLI does not silently create missing private paths.

```console
install -d -m 700 /data/ugh /data/ugh/private
python -m ugh_audit.invites issue --file /data/ugh/invites.json --user alice --token-out /data/ugh/private/alice-invite.token
```

Use paths appropriate for the deployment and restrict their filesystem access.
On Windows, create the directories with service-account-only ACLs first. Keep
token outputs outside the repository even though `.token` files are ignored.
Deliver the token through the operator's private credential channel. It is an
external-service authentication credential, not a key that unlocks the Agensi
skill. Do not put the registry or token file in Git, the upload ZIP, public logs,
or chat. Check the CLI help for rotation and revocation operations in the reviewed
commit; do not edit hashes by inventing a token format.

Persist `UGH_AUDIT_DB` and `UGH_USAGE_DB`. All workers governed by one global quota
must share the same usage SQLite file on a supported shared local volume. Separate
replicas with unrelated files do not share a global limit. An ephemeral filesystem
would lose history and counters on redeploy. Existing NULL-owned history remains
available only through trusted local/admin reads, not to invite users.

Current defaults from `beta_access.py` are 60 requests/minute/user, 100 admitted
audit attempts/day/user, 500/day globally, one concurrent audit/user and four
globally. Daily limits use UTC. Failed model calls and disconnected clients can
still consume admitted attempts. Limits count attempts, not currency; they do
not guarantee a monetary spending cap. Hosting and Anthropic costs remain the
operator's responsibility even when the Agensi skill is free.

Set `ANTHROPIC_API_KEY` only on the server. It is required for meaningful semantic
assessment even when the caller supplies requirements. Missing credentials cause
degraded results, not offline acceptance. Optional `auto_generate_meta` also uses
Anthropic, but the packaged pilot skill disables it and uses reviewed requirements.

## Connect and verify before inviting users

```console
python -m uvicorn ugh_audit.server:app --host 127.0.0.1 --port 8000
```

Expose the service through an authenticated-by-application HTTPS deployment or
reverse proxy; the remote MCP route is `/mcp/` (keep the trailing slash). Configure
a client capable of Streamable HTTP plus an Authorization Bearer header. OAuth connector support is
not implemented, and general ChatGPT connector compatibility is not promised.
An external URL is intentionally absent until an actual deployment is verified.

Set `UGH_ALLOWED_HOSTS` to the real public Host value, including its port when
present; comma-separated hosts are supported. This keeps the MCP transport's DNS
rebinding protection enabled. If a client sends an Origin header, explicitly allow
its exact origin using `UGH_ALLOWED_ORIGINS`, for example
`https://trusted-client.example`. A client sending no Origin remains permitted;
an empty list does not allow arbitrary supplied origins. Do not use a wildcard or
disable the transport protection to hide a connection mismatch.

Before listing, confirm unauthenticated access is refused except `/health`, test
two separate invite identities cannot retrieve each other's records, exercise
quota refusals, and inspect one authorized synthetic schema 4 audit. A model call
has an external cost; perform it only under the operator's approved budget. A
successful `/health` alone does not prove semantic assessment or database migration.

Record the deployed commit, smoke-test result and retention/contact policy. Verify
the selected agent's actual connection before advertising compatibility. Nothing
in this preparation confirms those deployment checks have occurred.
