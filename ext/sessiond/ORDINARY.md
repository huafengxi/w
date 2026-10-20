# Ordinary W → socket → P → PIPE → pi

`bootstrap.configure()` selects ordinary factories before HTTP listening. The
CLI consumes the same caller configuration. Missing/bad service configuration
fails startup; production never falls back to W-owned pi PIPEs or test adapters.
Registry passes explicit factories to Bridge. Independent P owns every pi PIPE.

## Minimal temporary session

```text
run/sessiond/<name>/
  <name>.agent      Web-owned declarative configuration and presentation
  <name>.jsonl      pi-owned history (may not exist until first assistant output)
  host.json         Web-owned accepted native P identity
  s                 fixed Unix socket
  host.log          optional shared P/pi diagnostics, not lifecycle authority
```

No per-instance directories, random instance UUIDs, binding/current/record
mirrors, input-environment snapshots, separate pidfiles or facts logs are created.
The session remains temporary: it is not backed up/synchronized as persistent
user data. Inbox identity stays at the caller's existing participant address;
ordinary registration does not create an agentd spec/pid/enable/watcher.

Managed `.agent` declarations contain the following fields:

| Field | Meaning |
|---|---|
| `host` | Declared machine name, not a process identity |
| `profile` | Caller-selected interactive profile |
| `cwd` | Working directory resolved from that profile |
| `sessionDir` | Directory containing this session's history |
| `name` | Stable generated session name, also used by the directory and filenames |
| `createdAt` | Creation time in UTC |
| `title` | Optional display-title override |

There is no `h2Registration` or `runtime` field. Rename changes only `title`;
it does not change `name`, the directory, filenames or URL. Hand-written external
declarations follow the optional-field rules in [ARCHITECTURE.md](ARCHITECTURE.md#9-路径与声明).

Web writes `.agent` for creation and title changes, and `host.json` after
accepting P. Lifecycle operations never rewrite the declaration. P only reads
host.json when checking a previous socket owner; it creates the socket and writes
diagnostics. Web control state references actual P identity and holds acceptance,
explicit operation fences and actual Web wait results in process memory only.
Pi identity and known stop descendants are also kept in memory only;
tool ancestry is sampled from the kernel when needed. P waits for pi in memory
and exits with the corresponding code. Child waits and error details are not
persistent JSON fields; diagnostics stay in host.log and request errors.
The HTTP `gen`/`expected_instance` slots serialize native P boot/PID/start identity,
not an extra generated token or a runtime generation. Names remain reserved by
their retained inboxes after deletion, and cached clients revalidate declarations.

`host.json` is a flat process identity object with `pid`, `start`, `boot`, `uid`,
`pgid`, `sid`, `ppid` and `state` at the top level; there is no `host` wrapper.
Parent and state values are startup snapshots; observations read current kernel facts.
`start` is a platform-local process start token, encoded as a string. `boot`
distinguishes system boots; Linux start ticks alone cannot distinguish them.
The file contains no binding, pi PID, child wait, error or launch-command fields.
P receives its executable, arguments, history path, cwd and environment once
through launch stdin; that input is not persisted as another runtime file.

## Start and crash boundary

W explicitly spawns P, gives input JSON once on stdin, then verifies the actual
P's kernel socket peer and one uniquely paired successful `get_state`, then
publishes only P identity in host.json. Default
acceptance timeout is 25 seconds for extension initialization. There is no model
prompt, command replay or probe-reconnect loop.

**No persistent launch intent is written before Popen.** A directory exists
because a session was created, not to mark an attempt; diagnostic log existence
is never treated as a launch marker. If Popen itself fails before creation, the
declaration stays unchanged. Actual post-spawn failures may be recorded. A Web
crash before Web publishes the accepted P identity can leave an ambiguous/residual
process; manual investigation is accepted instead of a new lock/lease/recovery
state machine. Socket checks still reject occupied/unknown endpoints, but do
not promise cross-Web simultaneous-start uniqueness.

Web exit/browser close only detaches. A new Web can reconnect to an identified
live P. Socket EOF is not pi exit. Native P identity, current peer/uid and the
declaration are checked on reconnect; unreadable/reused identities remain unknown.
Confirmed normal dormancy requires Web's true non-signal P wait and closed
known/group identities. Losing the spawning Web
loses in-memory operation fences and wait results: a later Web observes the live
P afresh and must not invent a host exit code. A dead P without an observed wait
remains unknown until explicit checked recovery.

Pi belongs to P's lifetime. Linux arms SIGKILL on parent death before exec,
checking the parent on both sides of prctl; this also covers uncatchable P exit.
Other supported hosts use native pi RPC's stdin-EOF shutdown contract. P retains
the sole stdin writer. A P crash cannot manufacture a pi wait result, and
remaining tool processes still block destructive operations until closure.

Live tools are read-only observations after verified ancestry to current P
with matching boot/uid/group/session. This does not persist adopted children or
expand signal authority. Unknown members or leader loss with residuals remain
unknown. Stop/reload/clear keep exact identity checks and confirmed tree closure.
Failed destructive operations remain fenced in the current Web process and are
not automatically retried.

## CRUD and file identity

Create succeeds after both declaration and inbox exist; an incomplete pair
cannot launch. Rename changes only title. Reload preserves history; clear
truncates only after closure. Stale native process expectations fail instead
of mutating a replacement.

Delete revalidates declaration/config and history identity around confirmed
stop, removes the exact declaration/history and known host state/log files, and
removes an empty session directory. It never recursively deletes unrelated
files or inbox contents. A socket left by a terminated P can be removed only
with the original inode and confirmed owner closure; replacement endpoints cause
refusal. Cached clients/streams detect missing declarations, so no extra
delete-tombstone file is needed. Failures can be partial and are surfaced, not
rolled back or silently repaired.

## External declarations and old layout

A hand-written declaration outside the managed temporary family still resolves
its history canonically. Such histories use the caller's short private
`W_SESSION_RUNTIME/<canonical-history-hash>/` for P state/socket/log, so aliases
share one host rather than allocating per-declaration endpoints. They do not
acquire the managed CRUD surface. Native P identity and kernel peer checks remain mandatory.

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
choosing the endpoint. Ordinary operations use only process-local thread locks. Task/bot RPC
routing and lifecycle remain with agentd, not this ordinary path.

## Regression entry points

`ordinary_default.py` audits real Popen boundaries, minimal files/single writers,
actual identity generations, stale callbacks, missing state, explicit history
adoption and pre-spawn failures. `ordinary_pi_http.py` uses real W HTTP/auth,
socket/P/isolated pi and a deterministic localhost model for restart attach,
reload/regenerate/clear/delete. The H2 lifecycle/startup/CRUD/repair suites retain
identity and failure countercontrols; historical file-lock claims are superseded.
