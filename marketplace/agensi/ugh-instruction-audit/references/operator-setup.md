# Operator setup for the invitation pilot

This is an operator guide included with the skill's text references. Preparing
the ZIP does not deploy a service or publish a listing. A real external HTTPS
endpoint, private invitations, retention policy and operating budget are still
needed. Agensi does not run this server.

## Install the reviewed implementation

Use Python 3.10+ and the exact reviewed commit with invitation-beta BYOK support, not an
old package bearing version `0.3.0`. Replace `APPROVED_BYOK_BETA_COMMIT` below
with that PR's full immutable commit SHA once review is complete. It must contain
`ugh_audit/beta_access.py`, `usage_limits.py` and `invites.py`.

```console
git clone https://github.com/Yuu6798/ugh-audit-core.git
cd ugh-audit-core
git checkout --detach APPROVED_BYOK_BETA_COMMIT
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

For deployments managed by `UGH_INVITES_JSON`, the environment registry is the
source of truth and is reapplied on startup. The CLI refuses issue/revoke while
that variable is present. Change the environment's hashed registry and redeploy,
then verify revocation with the old token. For offline registry preparation only,
explicitly unset that variable before using the CLI and copy the resulting hashed
registry back to the deployment configuration. Editing only the running file is
not a persistent revocation in this mode.

Persist `UGH_AUDIT_DB` and `UGH_USAGE_DB`. All workers governed by one global quota
must share the same usage SQLite file on a supported shared local volume. Separate
replicas with unrelated files do not share a global limit. An ephemeral filesystem
would lose history and counters on redeploy. Existing NULL-owned history remains
available only through trusted local/admin reads, not to invite users.

Current defaults from `beta_access.py` are 60 requests/minute/user, 100 admitted
audit attempts/day/user, 500/day globally, one concurrent audit/user and four
globally. Daily limits use UTC. Failed model calls and disconnected clients can
still consume admitted attempts. Limits count attempts, not currency; they do
not guarantee a monetary spending cap. Hosting costs remain the operator's
responsibility. Anthropic model calls are billed to each user's own API account;
free skill access does not include model credits. Users should set their own
provider spending limits.

Keep the server's `ANTHROPIC_API_KEY` unset or empty. In public invite mode every
audit requires the caller's own `X-UGH-Anthropic-Key`, separately from the
Authorization invite token. Even an accidentally configured server key is not
used as a fallback. Missing user keys block audits with `provider_key_required`
(REST HTTP 400) before model work. History and MCP initialization do not require
a provider key. The invite registry never contains provider keys.

The service uses provider keys only for the request and does not save them to
history or logs. Configure proxies, observability and clients to avoid logging
secret headers. The key reaches the operator's service before it is forwarded to
Anthropic; disclose this trust boundary and data handling to users.
Optional `auto_generate_meta` uses the caller's key and bypasses shared metadata
caches in public invite mode. The packaged pilot skill keeps it disabled and uses
reviewed requirements. Both paths still make paid semantic-check calls.

## Connect and verify before inviting users

```console
python -m uvicorn ugh_audit.server:app --host 127.0.0.1 --port 8000
```

Expose the service through an authenticated-by-application HTTPS deployment or
reverse proxy; the remote MCP route is `/mcp/` (keep the trailing slash). Configure
a client capable of Streamable HTTP with both an Authorization Bearer header and
the custom secret `X-UGH-Anthropic-Key` header. Environment substitution in the
example is client-specific; use the host's supported secret configuration.
OAuth connector support is not implemented, and general ChatGPT connector
compatibility is not promised.
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
quota refusals, provider-key-required rejection, and inspect one authorized
synthetic schema 4 audit with a user-owned API key and approved spending budget.
Check that a server environment key cannot enable a request lacking BYOK. A
successful `/health` alone does not prove semantic assessment or database migration.

Record the deployed commit, smoke-test result and retention/contact policy. Verify
the selected agent's actual connection before advertising compatibility. Nothing
in this preparation confirms those deployment checks have occurred.

## Retention and deletion readiness

Before accepting user data, verify the private contact's ability to receive requests
and establish the operating procedure in the repository's
[data handling and deletion policy](https://github.com/Yuu6798/ugh-audit-core/blob/main/docs/data-policy.md).
The creator-operated beta uses [kkoo6798@gmail.com](mailto:kkoo6798@gmail.com)
for private support and deletion requests. Ordinary mailbox send/receive history
has been checked. Production automatic cleanup was activated on 2026-10-29 UTC;
its initial cleanup and a separately authorized real-model audit passed the
[release checks](https://github.com/Yuu6798/ugh-audit-core/blob/main/docs/beta-release-checklist.md).
Acceptance of the manual identity/deletion/response procedure and the public
effective date remain pending; publication is on hold. Do not request API keys,
invitation tokens or full audit text by email.
The creator's proposed responsible operator, response procedure, maintenance
handoff and conditional effective date are in the
[manual operations proposal](https://github.com/Yuu6798/ugh-audit-core/blob/main/docs/beta-manual-operations.md).
The proposal is not an accepted duty assignment or permission to publish.
Self-hosted services must name and verify their own operator's contact.
The creator-operated beta is preparing a 30-day audit retention period, deletion
within seven days after identity verification, and backup removal within 30 days
after deletion. Other operators must disclose and implement their own applicable
terms. Do not claim that installing the server or loading this skill activates a
scheduled deletion process.

For a single POSIX host, the repository provides opt-in daily maintenance with
`UGH_RETENTION_AUTOMATION=1` and `python -m ugh_audit.deployment start`. The parent
stops and reaps its HTTP child before cleaning aged owner records, expired quota
rows, registered migration backups and known retired caches; it restarts HTTP
only on success. Cleanup runs at startup and every 23 hours, with brief downtime.
Review and explicitly register legacy backups before enabling it. Windows,
unmanaged concurrent servers, cloud snapshots and operator exports require
separate procedures. See the
[deployment guide](https://github.com/Yuu6798/ugh-audit-core/blob/main/docs/invite-beta-deployment.md).

Verify identity through the invitation's established private channel, without
requesting provider keys or tokens in a message. Plan maintenance, confirm the
affected owner and records before any deletion, and include backups and restored
copies in the procedure. Preserve other users' data and the service's quota and
invitation controls. Revocation prevents future access; it does not erase history.
Do not resume a restored service until previously required deletions have been
reapplied. Keep completion evidence without audit text or credentials.

The [release checklist](https://github.com/Yuu6798/ugh-audit-core/blob/main/docs/beta-release-checklist.md)
tracks these operating prerequisites separately from the real-BYOK audit and
post-publication Agensi delivery test. Preparing a ZIP authorizes none of those
external operations by itself.
