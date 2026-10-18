# Ordinary CRUD candidate — bllzca (unwired)

This is an additive W-only candidate above `8f38c82a9c304bd8f1a8a31c6d5c9cbe71b6bae6`.
P remains `89f40cc235e4e9e51b2b489124031697a3e23222`; its tool A remains
`b40e0d027b1ebbe072b0edc0929e0411da3c37af`. No default module imports this
factory, and no production CLI, HTTP route, UI or service is changed. The older
`h2_direct_host.installed` lifecycle-only fixture still rejects delete; use the
explicit **`h2_direct_host_crud.installed(options)`** for the new CRUD fixture.
It reuses that Lifecycle, strong identities, pidfds, peer check and P data socket.
The old 13-test suite remains intact and runs separately, not renamed as CRUD.

## Entry-point map and reproduction

- Original caller CLI `main(['new','--profile',...])` -> original
  `proc.create_cc_session` entry -> injected registry -> original profile/cwd/
  host validation and bounded name allocator/declaration writer. Creation is
  still CLI-only, NOT a new HTTP op. Caller source is supplied read-only and
  hash-checked (manifest); no caller implementation is copied into W.
- `api.interp` list_sessions/session_tabs/rename_session/delete_session -> same
  original API validation, result/HTTP shapes and title/tab formatting -> injected
  proc list/rename or Bridge delete entry. Original `bridge.get_bridge` resolves
  declarations and creates the same Bridge/RPC channel through an explicit factory.
- `api_call(... expected_instance=..., expected_registration=...)` captures the
  server-side test fences. This is NOT a change to default HTTP arguments, and
  does not claim default UI has a production fence transport already wired.
- `h2_direct_host_crud_ui.cjs` executes the actual unmodified doRename/doDelete/
  notifyTabsChanged source in a Node VM. Its api adapter makes real calls to an
  independent web process running original api.interp, with fixture-captured
  fences. Invalid title/stale delete produce real errors and no notification;
  successful rename/delete each post one same-origin tabs change and tabs are
  checked through the real API. This is not an HTTP/browser/model test. Only
  unrelated DOM/notice surfaces are fixture stubs, not the CRUD response.

```sh
/absolute/existing/python -B test/h2_direct_host_run.py \
  --p-source /new/fixed/P --a-source /new/fixed/A \
  --cli-source /readonly/original/session-ctl.py
```

Runner records env/cwd/argv/times/rc and copies the supplied caller source to its
new root, checks its hash and the P/A hashes, then runs 8 CRUD tests, old 13
lifecycle tests, original W regressions and P generic/dependency tests. Use short
outer TMPDIR and short canonical private runtime roots. Original full H1 runner
remains available for double-layout agent/socket/native-mock regressions; its
skip/unknown boundaries in HOST.md still apply. All roots are retained. Cleanup
uses only this run's exact Popen or recorded boot/start/uid/SID/PGID identities;
no production directory/socket is a fixture or deletion bait.

## Registry and state contract

A stable `registry.lock` flock in the same configured private runtime root wraps
creation, declaration/Bridge resolution, attach/query/start/send/change, list,
rename and delete. Web-local RLock and thread-local nesting share one fd per
outer operation. Lock order is registry -> Bridge/Supervisor operation ->
Lifecycle op flock. Existing P socket/session-key locks remain unchanged. No
registry daemon, spool, auto recovery, rund owner or agentd registration exists.

The `.agent` is still the declaration: only an opaque `h2Registration` UUID is
added, not a task/spec/pid or fabricated runtime generation. A Supervisor captures
that UUID; every potential start/send/change revalidates it and the declaration.
Profile, exact cwd, canonical JSONL, name and local host stay bound. After envscrub,
SESSIOND_SESSION_FILE, W_WORKSPACE, AGENTD_DIR, AGENTD_ROOT and interactive profile
are explicitly bound to this fixture's exact roots. Rename uses the original
title-only writer; it never changes name, JSONL, inbox or host instance.

Creation uses the original legal name policy and at most the original attempt
budget. Existing declarations, orphan histories, inbox owners and old runtime
bindings are occupied; no history/inbox is swallowed just because `.agent` is
missing. A retired name stays reserved while its inbox/runtime evidence exists.
The ordinary address is real legal `bot/<name>/inbox` only: no task registration,
spec, pid, enable, watcher, receiver ack or direct reaper bridge is synthesized.
A crash halfway through creation may leave an incomplete registration; it is
unknown, never silently reclaimed or auto-retried. No transactional rollback or
automatic creation repair policy is added.

List and tabs retain original override -> first user message -> name title
precedence and mtime/name ordering. Original pi/environ `host_map` is disabled for
this factory. State comes from the same canonical Lifecycle facts, without
opening a P socket, starting a host or obtaining an old web handle:

| Observation | list state |
|---|---|
| valid registered declaration, never-started runtime absent | dormant |
| strongly identified live host+pi, no unknown members | running (host PID) |
| confirmed closure plus real P child_wait and W wait, rc0 | dormant |
| same natural closure with actual nonzero child wait | failed |
| explicit fenced/failed operation | failed, not safe-to-delete proof |
| missing/incomplete record, unreadable/reused identity, missing real waits or invalid ownership | unknown |

Listing does not authorize deletion. Unreadable/non-object declarations may be
skipped by the original enumerator, but cannot become a safe dead process.
Runtime observations are not exit codes: no wait means no fabricated rc.

## Delete, file scope, fences and failure

Original API requires confirm=1 and rejects fixed fields. Candidate also requires
the captured registration UUID and exact current host instance, checked under
registry and Lifecycle locks. It never constructs a Bridge to delete and never
uses `shutdown(detach)` as proof of stopping. Identity observation precedes fence;
then durable delete fence -> exact cooperative tree stop/confirmation -> recheck
the two file identities -> original exact-file deletion -> durable tombstone.
Unknown identities, residual children/unknown ancestry, timeout/stop failure,
stale/duplicate/absent fences return visible original API errors; declarations
and history are untouched on those refusals. Failed fences are NOT auto-resumed.
An unopened declaration can be deleted with explicit `unstarted` capture, only
if there is no history/current record or unexpected runtime artifact; no host or
fake host instance is created for this case.

Only the exact non-symlink regular `.agent` and paired `.jsonl` are removed, by
the original proc deletion helper. In-directory aliases as well as escaping
symlinks are rejected. Original inbox (including messages), unrelated history,
run declarations/records/logs and stable lock inodes are retained; there is no
recursive name scan or directory cleanup. Original two-file unlink errors are
visible and may be partial after confirmed stop; this candidate does not promise
an atomic two-file filesystem transaction or restore deleted history.

Same-web Bridge is detached and its original event stream terminated only after
confirmed deletion. Other webs' original iter_events checks the durable tombstone
at the next existing event/keepalive boundary (at most the original 15s idle wait),
ends the stream and releases its subscription. Their cached Supervisor cannot
revive or send: revalidation plus the old instance fence rejects it. Concurrent
attach either finishes before deletion or receives an error after; it cannot
spawn behind a delete. A late delete after reload cannot touch the new instance;
UUID additionally prevents old registration requests from targeting a replacement.
Web exit remains detach only, P owns all pi PIPE/wait/pump, no automatic restart
or continuation is introduced. Reload/clear retain the old stop-confirm -> same
history operation -> explicit new instance sequence and weak claim semantics;
no backup/automatic restore contract is added.

## Full rollout matrix carried forward (not a complete H2 claim)

| Row | This candidate / required successor |
|---|---|
| create/list/rename/delete, stable short name/title/JSONL/inbox, dynamic tabs | offline fakepi original CLI/API/Bridge path and actual UI functions; real UI/HTTP remains B |
| attach/baseline/RPC id/watermark/SSE/gap/subscription release/commands/inspect | original modules and applicable fakepi regressions; real pi/HTTP/browser remains B |
| public history editing, same-JSONL append-only branch, native/dialog/queue/ask | original regressions only; true native/profile/persona/caps loading and public API conversations remain B |
| receiver followUp/steer, durable strong ack/isolation/crash rescan | UNTESTED; real receiver/user entry/ts+claimedTs required; no hand-written strong ack |
| real caller dispatch/ask/reply, explicit queue/dispatcher reaper return | UNTESTED; real existing caller/queue consumer chain required, no fake registration or direct bridge |
| web death/reopen same instance, no-client pump, once-only effect, identity/fences/concurrency/unknown | old 13 lifecycle plus new real independent OS-web CRUD tests; repeat with real pi/HTTP/browser in B and per-host deployment |
| reload/clear/delete failures, late duplicates, host/pi natural exit/crash | offline real cooperative fakepi + explicitly labelled identity injections; no auto recovery/fabricated exit facts |
| macOS identity/socket peer/exact stop/P deps/real runtime | UNTESTED; Linux /proc/SO_PEERCRED/pidfd boundary is NOT mac support |
| default chain/service version/all registered hosts deployment | UNTESTED; C preparation, then per-host D and independent E; do not expose default/version before propagation and busy-session risks are resolved |

P14/marked HTML/S58⑤ skips, historical F-A/S34/F-C S41 unknowns,
ResourceWarning, native internal-signal limitation and original weak claim remain
separate. This candidate does not close those gates. Runtime-root must be stable
and local/private across all callers; no cross-root/host/global arbitration or
noncooperative daemonize guarantee is claimed.

## 不做面（本任务原文）

1. 仅W test/h2_direct_host*未接线CRUD/registry候选；不改活动~/m/w、默认模块/路由/CLI、默认refs/bare HEAD/保全历史refs，不改P/A/S/C/平台或主仓/lore/服务profile/Makefile/部署配置。
2. 不新增rund长期owner/控制RPC/编排器/命令spool/跨机接管/自动pi重启或续回合，不让普通会话依赖agentd登记，不双控agentd task/bot，不恢复旧设计的rund强owner前提。
3. 不削弱强启动身份/同canonical稳定root锁/实例fence/精确停确认/unknown拒破坏；不凭裸PID/PGID/名字盲杀，不伪造退出码、Sgen、任务状态、spec/pid/watcher或强ack，不造直接reaper活性桥、不读ack职位信箱。
4. 不删除现有真实历史/会话，不连接生产或他人socket，不以生产根作harness/清理/拒删实验；本件无真实模型/HTTP/browser/服务启停/真实同步/GC/下载或安装，无电源策略修改。
5. 不把本件PASS外推完整H2、native/profile/receiver/caller/真实浏览器/macOS或生产；原P14/marked HTML/S58⑤skip、F-A/S34/F-C S41未知、ResourceWarning、native内部signal限制和旧claim弱语义分别保留。
6. 不回退/暂存/吞并/提交他人改动，不hard/stash/无pathspec amend；origin只merge，显式自家pathspec提交+push，commit message含bllzca；不再分发、不代写协议终态。
