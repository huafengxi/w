#!/usr/bin/env python3
# agentd 报表实时 API 的 Cmd backing 脚本（任务 0830-1700-a9vq）。
#
# fstab 挂载：/api/agentd-report Cmd python3 w/ext/agentd/bin/agentd-report.py
# 规范地址：GET /api/agentd-report/report.md（Cmd 挂载语义要求挂载点之下有子路径，
# backing 忽略该子路径；.md 后缀使无 ?v= 时 content-type 即 text/md）。
#
# 行为：按需执行 python3 ~/m/agentd/report.py（不带 --out），stdout 原样返回。
# 超时上限 15s；非零退出/超时/启动失败 → 返回错误说明 markdown（进程仍退出 0，
# 不让框架 500 裸抛）。认证由全局 BasicAuth（core/wsgi.py）继承，脚本不碰。
# 无缓存（每次请求现算）：报表生成 ~0.1s 只读，消费方仅仪表板一刷，不值得加缓存。
#
# cmd_store.py 调用形式：python3 w/ext/agentd/bin/agentd-report.py <path>（path 被忽略）。
#
# 测试钩（仅用于验证错误/超时路径，生产不设）：
#   AGENTD_REPORT_PATH     覆盖 report.py 路径
#   AGENTD_REPORT_TIMEOUT  仅可向下调：实际超时 = min(15, 值)，上限锁死 15s
import os
import subprocess
import sys

REPORT_PATH = os.getenv('AGENTD_REPORT_PATH') or os.path.expanduser('~/m/agentd/report.py')
TIMEOUT = min(15.0, float(os.getenv('AGENTD_REPORT_TIMEOUT') or 15))
STDERR_LIMIT = 2000


def error_body(title, detail):
    return ('# agentd 报表生成失败（实时 API）\n\n'
            '> **%s**\n\n```\n%s\n```\n' % (title, detail)).encode('utf-8')


def main():
    try:
        r = subprocess.run(['python3', REPORT_PATH],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        sys.stdout.buffer.write(error_body(
            'report.py 执行超时（上限 %gs）' % TIMEOUT,
            'cmd: python3 %s' % REPORT_PATH))
        return 0
    except OSError as e:
        sys.stdout.buffer.write(error_body('report.py 启动失败', '%r' % e))
        return 0
    if r.returncode != 0:
        stderr = r.stderr.decode('utf-8', 'replace')
        if len(stderr) > STDERR_LIMIT:
            stderr = stderr[:STDERR_LIMIT] + '\n…(stderr 截断)'
        sys.stdout.buffer.write(error_body(
            'report.py 非零退出: exitcode=%d' % r.returncode, stderr or '(无 stderr)'))
        return 0
    sys.stdout.buffer.write(r.stdout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
