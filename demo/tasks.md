# -*- type=script -*-
import os, json, time, glob

AGENTS_DIR = '/home/yuanqi.xhf/m/agents'

def interp(store, **kw):
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    rows = []
    for d in glob.glob(os.path.join(AGENTS_DIR, '*')):
        try:
            pid_path = os.path.join(d, 'pid.json')
            if not os.path.isfile(pid_path):
                continue
            with open(pid_path) as f:
                pid = json.load(f)
            task_id = os.path.basename(d)
            name = '-'
            spec_path = os.path.join(d, 'spec.json')
            if os.path.isfile(spec_path):
                try:
                    with open(spec_path) as f:
                        name = json.load(f).get('name') or '-'
                except Exception:
                    name = '-'
            status = pid.get('status') or '-'
            exitcode = pid.get('exitcode')
            exitcode_s = str(exitcode) if exitcode is not None else '-'
            final = 'yes' if pid.get('final') else 'no'
            report = '✅' if os.path.isfile(os.path.join(d, 'report.md')) else '❌'
            last = pid.get('endedAt') or pid.get('lastAliveAt') or pid.get('startedAt') or '-'
            rows.append((last, task_id, name, status, exitcode_s, final, report))
        except Exception:
            continue
    rows.sort(key=lambda r: r[0], reverse=True)
    lines = [
        '# 任务状态',
        '',
        '> 生成时间: %s · 共 %d 个任务（按最近心跳/结束时间倒序）' % (now, len(rows)),
        '',
        '| taskId | name | status | exitcode | final | report | 最近心跳/结束 |',
        '| --- | --- | --- | --- | --- | --- | --- |',
    ]
    for last, task_id, name, status, exitcode_s, final, report in rows:
        lines.append('| %s | %s | %s | %s | %s | %s | %s |' % (task_id, name, status, exitcode_s, final, report, last))
    return '\n'.join(lines) + '\n'
