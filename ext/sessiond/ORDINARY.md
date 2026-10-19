# Ordinary W → socket → P → PIPE → pi

`bootstrap.configure()` selects ordinary factories once, before HTTP listening.
The CLI consumes the same caller web profile and configures the same factories.
Production never imports the H2 test adapters or unittest.mock. Missing/bad
service configuration fails startup; there is no silent fallback to W-owned pi
PIPEs. The retained old Supervisor/Bridge are implementation bases and legacy
unit-test seams, not the default service transport after configuration.

Required caller inputs are W_SESSION_HOST_ENTRY (independent P's pi_host.py) and
W_SESSION_RUNTIME (resolved to a short, canonical, private local directory), plus the existing
W_WORKSPACE/W_AGENTD_DIR/host map/pi configuration. Caller workspace aliases are
resolved before binding/identity checks; the private-directory check itself is
not relaxed. P itself never imports A.
The default startup acceptance timeout is 25 seconds to include pi extension
initialization. It remains one post-launch peer-checked get_state, not a retry
or pre-launch contention protocol. Ordinary operations take only thread locks.

Read-only running status accepts live tool descendants only after checking their
current identities and ancestry back to the recorded P/pi, with matching boot,
owner, process group and session. These observations are not persisted and do
not expand stop authority. Unrelated members, unverifiable ancestry and residual
processes after leader loss remain unknown. Tool execution therefore does not
temporarily reject metadata/control RPC merely by starting a subprocess.

HTTP reload/clear carry the attached instance token (`expected_instance`), not a
rund generation. API forwards the explicit expectation to the operation gate;
stale/empty values fail rather than mutate a different instance. Delete manual
URLs can capture the current registration/instance server-side without starting
P; when an expectation is supplied it is enforced. Agent SocketSupervisor and
agentd control routing remain unchanged.

CLI list/autorun do not create runtime roots or missing binding records. Service
startup and explicit create/attach may create private state. Process/RPC EOF is
not proof of pi exit; failed/unknown records remain unknown and are not retried.

## Existing-history cutover

Old histories lacking runtime evidence do not become safely dormant merely
because a file is present and no current.json was found. An operator must close
recorded old owners, back up and preserve the history bytes, validate the local
declaration/inbox, then explicitly write imported-history.json in its private
binding directory. Its binding and history dev/inode/size/mtime must match and
no launch artifacts may exist. This is offline adoption evidence, not a fake
P pid, wait result, lease or claim to atomic cross-Web ownership.

The permission is consumed after recording the first real launch and BEFORE
Popen. After that, losing current.json must not reuse the import permission.
No normal request automatically imports history or repairs incomplete records.
Deployment tools belong to the caller, not this mechanism repository.

The ordinary host/registry live in ordinary_host.py and ordinary_registry.py.
H2 test files are thin, scoped adapters for those same runtime globals. The
fixed-input runner includes ordinary_default.py: no install()/patch, default
CLI/API, explicit fences, preserved history, one-use import and zero file locks.
