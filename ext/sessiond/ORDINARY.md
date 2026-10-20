# Ordinary W → socket → P → PIPE → pi

`bootstrap.configure()` selects ordinary factories before HTTP listening. The
CLI consumes the same caller configuration. Missing/bad service configuration
fails startup; production never falls back to W-owned pi PIPEs or test adapters.
Registry passes explicit factories to Bridge. Independent P owns every pi PIPE.

## Minimal temporary session

```text
run/sessiond/<name>/
  <name>.agent      Web-owned configuration, registration and actual control results
  <name>.jsonl      pi-owned history (may not exist until first assistant output)
  host.json         P-owned native P identity and real pi exit result
  s                 fixed Unix socket
  host.log          optional shared P/pi diagnostics, not lifecycle authority
```

No per-instance directories, random instance UUIDs, binding/current/record
mirrors, input-environment snapshots, separate pidfiles or facts logs are created.
The session remains temporary: it is not backed up/synchronized as persistent
user data. Inbox identity stays at the caller's existing participant address;
ordinary registration does not create an agentd spec/pid/enable/watcher.

Web is the sole writer of `.agent` (including its `runtime` control section).
P is the sole writer of `host.json`. Web control records reference actual P
identity and hold acceptance, explicit operation fences and actual Web wait
results. Pi identity and known stop descendants are kept in memory only;
tool ancestry is sampled from the kernel when needed. P's real child wait
records an exit result without a durable pi PID.
The HTTP `gen`/`expected_instance` slots serialize native P boot/PID/start identity,
not an extra generated token or a runtime generation. Registration UUID remains
separate, preventing stale clients from targeting a different session object.

## Start and crash boundary

W explicitly spawns P, gives input JSON once on stdin, then verifies the actual
P's kernel socket peer and one uniquely paired successful `get_state`. Default
acceptance timeout is 25 seconds for extension initialization. There is no model
prompt, command replay or probe-reconnect loop.

**No persistent launch intent is written before Popen.** A directory exists
because a session was created, not to mark an attempt; diagnostic log existence
is never treated as a launch marker. If Popen itself fails before creation, the
declaration stays unchanged. Actual post-spawn failures may be recorded. A Web
crash before P publishes enough identity facts can leave an ambiguous/residual
process; manual investigation is accepted instead of a new lock/lease/recovery
state machine. Socket checks still reject occupied/unknown endpoints, but do
not promise cross-Web simultaneous-start uniqueness.

Web exit/browser close only detaches. A new Web can reconnect to an identified
live P. Socket EOF is not pi exit. Native P identity, current peer/uid and session
binding are checked on reconnect; unreadable/reused identities remain unknown.
Confirmed natural dormancy still requires P's true pi wait, Web's true P wait,
consistent results and closed known/group identities. Losing the spawning Web
can lose its future wait authority: a later Web must not invent a host exit code.

Pi belongs to P's lifetime. Linux arms SIGKILL on parent death before exec,
checking the parent on both sides of prctl; this also covers uncatchable P exit.
Other supported hosts use native pi RPC's stdin-EOF shutdown contract. P retains
the sole stdin writer. A P crash cannot manufacture a pi wait result, and
remaining tool processes still block destructive operations until closure.

Live tools are read-only observations after verified ancestry to current P
with matching boot/uid/group/session. This does not persist adopted children or
expand signal authority. Unknown members or leader loss with residuals remain
unknown. Stop/reload/clear keep exact identity checks and confirmed tree closure.
Failed destructive operations remain visibly fenced, not automatically retried.

## CRUD and file identity

Create publishes registration completion only after inbox creation succeeds;
partial registrations never launch. Rename changes only title and preserves Web
control data. Reload preserves history; clear truncates only after closure.
Stale process/registration expectations fail instead of mutating a replacement.

Delete revalidates registration/config and history identity around confirmed
stop, removes the exact declaration/history and known host state/log files, and
removes an empty session directory. It never recursively deletes unrelated
files or inbox contents. A socket left by a terminated P can be removed only
with the original inode and confirmed owner closure; replacement endpoints cause
refusal. Cached clients/streams detect missing/changed registration, so no extra
delete-tombstone file is needed. Failures can be partial and are surfaced, not
rolled back or silently repaired.

## External declarations and old layout

A hand-written declaration outside the managed temporary family still resolves
its history canonically. Such histories use the caller's short private
`W_SESSION_RUNTIME/<canonical-history-hash>/` for P state/socket/log, so aliases
share one host rather than allocating per-declaration endpoints. They do not
acquire the managed CRUD surface. Native binding checks remain mandatory.

Legacy flat temporary declarations are listed read-only, unknown, and rejected
by managed attach/CRUD until explicit offline migration. Old live P processes
are not stopped, moved or reconstructed by ordinary requests. An offline operator
must close old owners and preserve history before relocation/adoption. There is
no imported-history permission file or permanent legacy runtime implementation.
Existing history with missing current host facts is unknown to normal requests;
explicit offline adoption can call the lifecycle start after proving closure.

Required caller inputs remain `W_SESSION_HOST_ENTRY`, `W_SESSION_RUNTIME`,
workspace/agent tool directory, host map and pi configuration. Canonical private
directories and short AF_UNIX paths are checked; aliases are resolved before
binding. Ordinary operations use only process-local thread locks. Task/bot RPC
routing and lifecycle remain with agentd, not this ordinary path.

## Regression entry points

`ordinary_default.py` audits real Popen boundaries, minimal files/single writers,
actual identity generations, stale callbacks, missing state, explicit history
adoption and pre-spawn failures. `ordinary_pi_http.py` uses real W HTTP/auth,
socket/P/isolated pi and a deterministic localhost model for restart attach,
reload/regenerate/clear/delete. The H2 lifecycle/startup/CRUD/repair suites retain
identity and failure countercontrols; historical file-lock claims are superseded.
