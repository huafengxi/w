# Ordinary fixed socket: post-launch acceptance, no file locks

This is the current ordinary-host candidate contract. P now lives in the unified
agent-platform/pi-host module, runs independently with only the standard library,
and neither imports A nor needs AGENTD_DIR. Agent adapter/persona policy moved
to agent-platform/agentd; dependency direction is A -> P. W still uses its own
explicit A tool source for registration/other agent APIs; that is not a P dependency.

This contract supersedes the older
file-lock and cross-Web linearizability statements in h2_direct_host.md,
h2_direct_host_crud.md and h2_direct_host_platform.md. Those suites' historical
results remain historical; their old concurrency assertions are not current
promises.

## Start sequence

1. W explicitly starts its independent P, using the agreed fixed local socket
   pathname and private local runtime root. P binds the endpoint and starts pi.
2. W reads P's host_started facts and the OS start identities for this P/pi.
3. Only after pi exists, W connects once to that socket. Kernel peer PID/uid and
   recorded start identity must match the P returned by THIS Popen. A mismatched
   peer receives no RPC.
4. W sends one uniquely identified native get_state request. Only a matching
   response ID, command=get_state and success=true accepts the start. Other
   replayed events/responses are not proof. Rejection, EOF, malformed response
   and timeout fail startup; there is no probe reconnect/replay loop.
5. Before acceptance the record is verifying/unknown, never running. Success
   records startupAcceptance and then publishes running. A failed start records
   its error and only signals its own recorded P/pi identities. It never unlinks
   the endpoint or signals its actual foreign peer to win the pathname.

The probe is startup ownership/readiness acceptance, not pre-start contention
avoidance, a lease, CAS, a winner election or a uniqueness proof. No model prompt
is sent. A later Web reconnect still verifies the peer against its current
instance record; reconnect does not restart pi or replay user commands.

## Locks and publication

The ordinary path creates none of registry.lock, op.lock, s.lock or canonical
session.lock. Process-local RLocks remain to serialize threads and local API
adapters. Legacy lock files are neither acquired nor deleted. File locks in the
agent task/bot adapter remain in A. Generic P has no file-lock implementation;
A.Wrap adds its task-specific locks without P importing any agent code.

One canonical session uses ONE agreed endpoint. Supporting different endpoint
basenames for the same session is no longer a contract. Existing owner/stale
socket checks and bind errors remain: an occupied/unknown endpoint is not blindly
unlinked to retry. No additional pre-launch double-check sequence is introduced.
Simultaneous human actions from different Web processes are best effort, not
cross-process atomic operations. Ordinary instance/registration fences and
history-before/after checks remain single-operation validations; they do not
provide linearizability without a file lock.

Metadata uses distinct temporary files followed by atomic replace, so writers do
not corrupt each other's temporary file; this is NOT mutual exclusion. New
session declarations use no-overwrite link publication so a rare colliding
creator cannot overwrite someone else's declaration. Listing is not a global
snapshot. Stale requests, unreadable identities and failed stop still fail
visibly; there is no automatic retry of destructive operations.

## Tests

- h2_direct_host_startup_test.py: real correct peer/native response, actual foreign
  socket swap with no RPC/kill/unlink of that peer, wrong ID, rejected/wrong
  command, EOF, timeout, no automatic failed-start retry, unique atomic temps.
- Adapted lifecycle13/CRUD8 retain sequential/reconnect/history/unknown/error
  behavior. Cross-Web simultaneous startup/reload/delete guarantees and
  alternate-socket arbitration were explicitly removed, not silently passed.
- P test_local_process checks the generic Transport creates no locks;
  test_standalone copies P alone and rejects A imports/reads during real RPC.
  A test_rpc_adapter separately checks actual agent two-lock contention and
  shared Transport method objects; the UI baseline implementation moved, not copied.
- Fakepi records each startup get_state request. Raw fixture clients match their
  own response ID instead of mistaking the startup response replay for success.

All roots are new/private/retained, and cleanup is identity-scoped. These tests
are not production activation, a real browser/model/receiver/caller PASS or a
reopening of separately paused real-native B1 experiments.
