#!/usr/bin/env python3
# svc 服务状态报表实时 API 的 Cmd backing 脚本（任务 0830-1759-u8ql）。
#
# fstab 挂载：/api/svc Cmd python3 w/ext/svc/bin/svc-status.py
# 规范地址：GET /api/svc/status.md（Cmd 挂载语义要求挂载点之下有子路径，
# backing 忽略该子路径；.md 后缀使无 ?v= 时 content-type 即 text/md）。
#
# 行为：按需执行 python3 ~/m/svc/svc.py status，stdout 原样返回。
# 超时上限 60s（逐服务 make 调用，比 agentd report 慢）；非零退出/超时/启动失败
# → 返回错误说明 markdown（进程仍退出 0，不让框架 500 裸抛）。
# 认证由全局 BasicAuth（core/wsgi.py）继承，脚本不碰。
# 无缓存（每次请求现算）：报表 ~数秒但只读，消费方仅仪表板一刷（30s），不值得加缓存。
#
# cmd_store.py 调用形式：python3 w/ext/svc/bin/svc-status.py <path>（path 被忽略）。
#
# 测试钩（仅用于验证错误/超时路径，生产不设）：
#   SVC_STATUS_PATH     覆盖 svc.py 路径
#   SVC_STATUS_TIMEOUT  仅可向下调：实际超时 = min(60, 值)，上限锁死 60s
import os
import subprocess
import sys

STATUS_PATH = os.getenv('SVC_STATUS_PATH') or os.path.expanduser('~/m/svc/svc.py')
TIMEOUT = min(60.0, float(os.getenv('SVC_STATUS_TIMEOUT') or 60))
STDERR_LIMIT = 2000


def error_body(title, detail):
    return ('# svc 服务状态报表生成失败（实时 API）\n\n'
            '> **%s**\n\n```\n%s\n```\n' % (title, detail)).encode('utf-8')


def main():
    try:
        r = subprocess.run(['python3', STATUS_PATH, 'status'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        sys.stdout.buffer.write(error_body(
            'svc.py status 执行超时（上限 %gs）' % TIMEOUT,
            'cmd: python3 %s status' % STATUS_PATH))
        return 0
    except OSError as e:
        sys.stdout.buffer.write(error_body('svc.py 启动失败', '%r' % e))
        return 0
    if r.returncode != 0:
        stderr = r.stderr.decode('utf-8', 'replace')
        if len(stderr) > STDERR_LIMIT:
            stderr = stderr[:STDERR_LIMIT] + '\n…(stderr 截断)'
        sys.stdout.buffer.write(error_body(
            'svc.py 非零退出: exitcode=%d' % r.returncode, stderr or '(无 stderr)'))
        return 0
    sys.stdout.buffer.write(r.stdout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
