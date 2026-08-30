# -*- coding: utf-8 -*-
'''实时报表 API：直接 popen，替代 fstab Cmd 挂载 + 外置 backing 脚本
（任务 0830-1802-uyyh，用户 08-30 拍板：不要用 CmdStore，直接 popen）。

两个端点（URL 与原 Cmd 挂载时代保持一致，消费方 dash.itab 不改）：
  /api/agentd-report/...  现算 python3 ~/m/agentd/report.py（超时上限 15s）
  /api/svc/...            现算 python3 ~/m/svc/svc.py status（超时上限 60s）

行为：按需执行、无缓存；超时/非零退出/启动失败 → 返回错误说明 markdown
（HTTP 200，不裸抛 500）。认证由全局 BasicAuth（core/wsgi.py）继承。
挂载点下任何子路径均返回同一报表（与原 backing 脚本忽略子路径一致）。

测试钩（仅用于验证错误/超时路径，生产不设）：
  AGENTD_REPORT_PATH / AGENTD_REPORT_TIMEOUT（仅可向下调，上限锁死 15s）
  SVC_STATUS_PATH    / SVC_STATUS_TIMEOUT   （仅可向下调，上限锁死 60s）
'''
import logging
import os
import subprocess

STDERR_LIMIT = 2000

def _error_body(name, title, detail):
    return ('# %s生成失败（实时 API）\n\n> **%s**\n\n```\n%s\n```\n'
            % (name, title, detail)).encode('utf-8')

def _run(name, cmd, timeout):
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return _error_body(name, '%s 执行超时（上限 %gs）' % (' '.join(cmd), timeout),
                           'cmd: %s' % ' '.join(cmd))
    except OSError as e:
        return _error_body(name, '%s 启动失败' % ' '.join(cmd), '%r' % e)
    if r.returncode != 0:
        stderr = r.stderr.decode('utf-8', 'replace')
        if len(stderr) > STDERR_LIMIT:
            stderr = stderr[:STDERR_LIMIT] + '\n…(stderr 截断)'
        return _error_body(name, '%s 非零退出: exitcode=%d' % (' '.join(cmd), r.returncode),
                           stderr or '(无 stderr)')
    return r.stdout

def _spec_cmd(path_env, default_path, args):
    path = os.getenv(path_env) or os.path.expanduser(default_path)
    return ['python3', path] + args

# prefix -> (路径覆盖环境变量, 脚本默认路径, 脚本参数, 超时环境变量, 超时上限, 报表名)
_ROUTES = [
    ('/api/agentd-report', 'AGENTD_REPORT_PATH', '~/m/agentd/report.py', [],
     'AGENTD_REPORT_TIMEOUT', 15, 'agentd 报表'),
    ('/api/svc', 'SVC_STATUS_PATH', '~/m/svc/svc.py', ['status'],
     'SVC_STATUS_TIMEOUT', 60, 'svc 服务状态报表'),
]

def api_handler(env, path, query, post):
    '''wsgi handler 链中的一环：命中 /api/agentd-report 或 /api/svc 前缀则
    现算返回，否则返回 None 让后续 handler 接管。'''
    for prefix, path_env, default_path, args, to_env, to_cap, name in _ROUTES:
        if path == prefix or path.startswith(prefix + '/'):
            timeout = min(float(to_cap), float(os.getenv(to_env) or to_cap))
            body = _run(name, _spec_cmd(path_env, default_path, args), timeout)
            meta = dict(type=query.get('v') or 'text/md')
            logging.info('live_api: %s -> %d bytes', path, len(body))
            return meta, body
    return None
