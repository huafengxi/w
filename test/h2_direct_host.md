# H2 direct-host lifecycle candidate — task/4aqn6z

**Additive bllzca CRUD successor:** see [h2_direct_host_crud.md](h2_direct_host_crud.md)
for the explicitly installed registry/CLI/API/Bridge CRUD factory and its new tests.
The lifecycle-only factory and historical scope below remain available, including
its deliberate delete rejection; they are not the new CRUD entry point. Neither
factory is imported by default production modules.

**Unwired offline candidate, not a default W implementation, complete H2 PASS or deployment.**
Only `test/h2_direct_host*` files are added. Tests explicitly patch the original
Supervisor factory and Bridge class; original declaration, profile whitelist,
API interpreter, command validator, RPC ids/response matching, baseline,
watermark, SSE iterator/gap/subscriber budget and history editing implementation
are reused, not copied. Existing agent SocketSupervisor and its real agentd
control-proxy branch are not assigned ordinary syscall authority.

## Fixed inputs and reproduce

`h2_direct_host_manifest.json` freezes W base, P and A revisions plus the imported
P host/transport/locator and A proto/wait_state/envscrub/safe_delete hashes.
P is **unchanged**: H1 stdout `host_started` / `child_wait`, owner metadata and
cooperative same-parent canonical socket locks already supply the necessary
facts. No S/A/C changes or consumer pin changes are required.

With existing Python/Node and new fixed P/A exports only:

```sh
/absolute/miniconda3/bin/python -B test/h2_direct_host_run.py \
  --p-source /new/readonly/P-export --a-source /new/readonly/A-export \
  --cli-source /readonly/original/session-ctl.py
```

The runner verifies the listed input hashes, creates and retains a new outer
HOME/TMPDIR/cwd/workspace, records commands/env/cwd/times/rc and warnings/skips in
`results.json`, and runs 13 real fakepi lifecycle tests, applicable original W
regressions, and original P generic/dependency tests. No installed pi, model,
browser, HTTP server, production socket, sync, GC or service is used. All socket
paths, declarations, JSONL, logs, registry, locks and child environments are in
new roots. The runner additionally executes the bllzca CRUD suite; its original
CLI input hash is recorded in the updated manifest. Native H1 double-layout/shared-agent/socket regressions can separately
be run using unchanged P `recovery/h1-run-tests.py`; see P HOST.md.

The fakepi and client are actual independent OS processes. The fixture's Popen
stdin pipes only carry harness-to-web test requests; they are **never** the pi
PIPE. The harness is a Linux subreaper solely to wait for its own orphan test
identities, not a product owner. Cleanup uses exact Popen or recorded
PID+boot/start+uid/session/group with pidfds, never a name scan or killpg.
Evidence and failed roots are retained. Deletion guard calls use harmless bait
created in the new runner root, not production.

## Responsibility / field contract

| Owner | Authority / facts |
|---|---|
| W finite Lifecycle | stable declaration/binding, local op flock, explicit P Popen, expected-instance fence, identity checks, explicit per-identity stop, confirmed historical truncate/claim order |
| P H1 Host | one pi Popen, all pi stdin/stdout/stderr PIPE, continuous pumps, one replaceable data socket, real pi wait; no auto restart or W control RPC |
| OS | PID + `/proc` start + boot_id + uid + session/group, pidfd target stability, process state, SO_PEERCRED |
| Original Bridge | web-local RPC waiter ids, baseline/watermark and SSE ring/namespace; NOT process identity or runtime generation |
| Harness only | containment/subreaping/independent collection of its own recorded synthetic load |

`runtime` is an **explicit canonical private local root, identical for every web,
alias and recovery caller**. The canonical JSONL SHA maps to one fixed short
parent and socket `s`. Parent does not vary with web or instance. Binding freezes
canonical session, legal participant short name and cwd. `title` is presentation
only; `.agent` / JSONL / pi name / `bot/<name>/inbox` remain the same identity.
The fixture supplies inbox-only and explicitly sets AGENTD_ROOT to the synthetic
workspace containing that agents directory; it creates no spec/pid/enable/watcher/queue
registration. Real receiver/caller injection and queue reaper round trips are
not exercised by this lifecycle candidate (I0=A remains).

No global arbitration across deliberately different runtime roots, hosts,
filesystems or hostile same-uid metadata writers is claimed. This is the same
stable-parent boundary as H1, not a new session family or global registry.
Directory is private/canonical and socket length bounded. Lock inodes persist;
W never unlinks socket or lock files. H1 alone performs its verified socket
cleanup/takeover. Unknown endpoint/missing owner/incomplete startup/reused PID/
unreadable identity/config binding mismatch cannot trigger blind takeover.

Each launch creates an immutable random `instance` directory under the op lock;
`current.json` points to the current record, and each run retains declaration,
record, stdout/stderr. Instance token is real lock-allocated startup identity,
**not Sgen, agent pid, connection count or a fabricated runtime generation**.
The original API's `gen` slot carries this ordinary instance string in the
candidate only, for equality/fencing, not ordering. `pid` is explicitly host PID;
`child.pid` is pi. No task pid is written.

After original envscrub, the adapter explicitly reinstates the original probe/edit
binding SESSIOND_SESSION_FILE (canonical JSONL), W_WORKSPACE and fixed AGENTD_DIR;
profile is injected only after its original whitelist check. This preserves the
existing inspect/public edit contract, not proof of real native extension loading.

P starts with `start_new_session=True`, stdin=DEVNULL, stdout/stderr regular files
independent of web lifetime. W calls no pi PIPE constructor; all ordinary data
travels over existing P data socket. The actual connection validates
SO_PEERCRED against the stored real host start identity and rechecks instance
under the lock. No extra health-check connect steals the active client.
W restart needs no old Popen handle. Socket replacement/EOF is only connection
loss: `sessiond.channel_disconnected`, not evidence of natural pi exit.
Reconnecting retains the same instance; a Bridge lifecycle announcement resets
its local baseline/dialog namespace, not the underlying child. Old buffered
responses cannot be stamped with a newly installed local instance: original
queries hold the local operation lock and old reader is closed/joined before
installing a new one. Send checks current instance/fence under cross-process
lock; native RPC success remains necessary, and writes/timeouts are ambiguous.

A launch marker is written **before** Popen. A web crash in the gap without a
complete host+child identity leaves unknown and cannot be automatically retried.
The W that actually spawned a P has one ordinary daemon thread calling that
Popen.wait to reap its child, never to restart/monitor another invocation. It
persists `hostWait.source='W Popen.wait'` only from that true wait (or
`Popen.poll/wait` observation). The thread disappears with web; no long-lived
owner/control service is introduced to recreate lost wait authority.

`childWait` comes only from exactly one valid identity-matched H1
`host_started` and at most one paired `child_wait` JSON fact in this run's
exclusive stdout. Missing/truncated/duplicate/mismatched facts are unknown.
Host EOF/exit0/socket EOF never invents a pi returncode. `state=dormant` requires
true child wait + true host wait with consistent rc + settled known identities
and empty live group. A new web with no wait fact may attach a still-live host;
a dead/crashed host without true wait stays unknown. Explicit recovery is
allowed only after observed closure, and still does not invent missing rc.

## Finite stop, reload, clear, recover

All destructive calls require the captured `expected_instance` via the explicit
test `api_call` wrapper (or `change` / `Lifecycle.operate`). Missing/stale token
fails. Original agent control calls retain their original proxy semantics.
The current instance is rechecked under the same session op flock. A fence is
persisted before effects; all further sends/implicit starts reject that fence.
Duplicate or late requests for an old instance cannot kill a new one or clear
again. No request spool, command replay worker or automatic compensation exists.
After a failed destructive operation the fence is intentionally retained; this
candidate has no blind retry/unfence API. HTTP timeout may mean delivered; do
not automatically repeat a destructive operation (even with a new request id).

When the host leader is still strongly identified, stop enumerates the current
same-group/session cooperative foreground tree, verifies ancestry/uid/identity,
freezes exact pidfd targets, discovers/freeze-checks descendants to quiescence,
records the known identities, then sends TERM and CONT to those exact targets.
It never sends a bare PID/PGID/name signal. SIGKILL escalation is not needed by
the bounded contract: uncooperative/timeout fails conservatively. Leader lost
with residual group refuses signalling, even if a remembered child remains.
New/unknown residual members, changed known descendant group/start, unreadable
/proc, missing identities or timeout prevent success, truncate and replacement.
Known stopped targets are resumed on a failed freeze; unknown targets are not
signalled. Actual no-live-known-identities + empty live group is checked; TERM
sent is not “stopped”. Zombies are terminated observations, not fake wait facts;
new launch additionally requires old host PID reaped because H1 treats a
still-present owner conservatively. Noncooperative daemonization outside the
known cooperative group is not supported, as in H1; this is not an arbitrary
process-tree supervisor or lease replacement.

- `stop`: fence → confirm closure → stopped; no pi exit-code fabrication.
- `reload`: fence → stop confirmation → reader closed/joined → same history and
  original claim/header preparation → new P/instance → real socket identity.
- `clear`: same stop/reader gate → original ordinary `open(a).truncate(0)` →
  original `_claim_origin` → new P. Truncate/raising claim/start errors propagate
  to original API 502 and no false success. No copy backup, retention promise or
  automatic restoration is added. **Original `_claim_origin` itself may log and
  swallow chown failures; this candidate does not claim a stronger guarantee.**
  Raising-claim injection tests are explicitly injections, not proof the old
  helper surfaces every chown failure. Already truncated history is not rolled
  back if a later step fails.
- `recover`: old instance expected + all known/group actually settled → explicit
  new P, preserved history. A failed prior operation remains fenced/manual;
  this candidate does not silently finish it or erase its evidence.

Only the test-only adapter is callable. Default create/list/delete still use
old implementation, and are **not** migrated or safe to expose as candidate
control APIs. `shutdown` in this injected supervisor means web detach, not a
lifecycle stop; explicit stop requires its token. The test-only api_call returns
501 for delete_session and shutdown(delete_session) returns an error, so original
bridge.drop_session cannot misreport a detach as a verified kill. Delete migration
is a separate full-H2 follow-on. No default route uses this factory.

## Retained original H2 matrix / nonclaims

| Matrix row | This first candidate |
|---|---|
| Declaration/profile/command validation, RPC baseline/watermark/SSE/gap | original functions reused; applicable regressions plus injected real fakepi chain |
| W death/reopen, P/pi/stdio identity, no-client pump, replacement | real independent OS clients and load |
| Stable canonical/symlink/same-parent endpoint locks | same-root W race plus direct H1 rejection cases |
| Ordinary instance/old-request fence | real lock-allocated token; not S runtime gen |
| Natural zero/nonzero/host crash/unknown/explicit recovery | real fakepi and known-identity collection; stale/unreadable identity explicitly injected |
| reload/clear running/concurrent/duplicate/failures | original API results and actual state/history; no receiver exactly-once contract |
| create/list/rename/delete migration and UI tab refresh | lifecycle-only factory NOT migrated; bllzca explicit CRUD successor covers offline fakepi and actual UI functions, NOT real browser/HTTP |
| Full profile/persona/assets, inspect and public edit on real pi | original modules only, not new real-pi proof |
| followUp/steer receiver durable strong ack and isolation | NOT tested; no fake ack; inbox-only fixture is not delivery |
| real caller dispatch/ask/reply/explicit queue reaper | NOT tested; I0=A preserved, no fake registration |
| true browser/model/native internal signals, cross-host/macOS/sync, Y1/production | NOT tested/authorized |

P14/marked HTML skips, original native internal signal limitation, historical
ResourceWarnings/S58⑤/F-A/S34/F-C S41 unknowns remain separate; this candidate
must not be used to erase them or label full H2/receiver/production PASS.
