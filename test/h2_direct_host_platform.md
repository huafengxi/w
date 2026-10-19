# Ordinary direct host platform adapter

The explicitly selected P's adjacent `ordinary_process.py` is the single source
of process start facts for P and W. `Lifecycle(host_entry=...)` loads this exact
file before launching anything; conflicting tool paths in one Web process are
rejected. The test manifest pins its content with the rest of P. No default
production route imports this candidate.

Linux retains `/proc` start ticks, boot UUID and pidfd signalling. Darwin uses
libproc start seconds/microseconds, UID/parent/group/state, getsid and
kern.boottime. AF_UNIX peer verification uses LOCAL_PEERPID and getpeereid,
then the existing current-instance/fence/host-identity checks under the lock.

## Darwin stop trade-off

Each Darwin STOP/TERM/CONT checks the recorded identity immediately before
`os.kill(pid, signal)`. This deliberately accepts the remaining check-to-kill
PID-reuse race; there is no claim of atomic object targeting. Read failure,
wrong identity or wrong boot still prevents signalling. Group enumeration,
cooperative descendant checks, finite freeze/termination, instance fencing,
unknown rejection and history ordering otherwise remain unchanged. This does
not promise correctness for daemonization, changed UID/group/session or hostile
same-uid peers.

## Tests and boundaries

`h2_direct_host_platform_test.py` checks wrong identity/read denial/boot rejection,
the Darwin check-then-kill path and a real owned child's stop. Existing lifecycle
and CRUD fixtures use the selected P helper for fakepi start facts. Only Linux
uses prctl subreaper containment; Darwin cannot supply an adoptive harness wait
for a process orphaned by a deliberately killed Web. Those Linux-only harness
cases must be reported separately, not rewritten into product wait facts.

All tests retain their fresh synthetic roots. No existing user history, agent
registration or production socket is a fixture. Platform/CRUD fakepi results do
not prove real native/receiver/caller/browser behavior, default activation or
production deployment. Those gates have their own evidence.
