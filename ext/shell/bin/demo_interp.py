#!/usr/bin/env python3
# Demo interp for markdown.html inline commands.
# Invoked by rpc/cmd.py as:  demo_interp.py <md-file-path> <cmd-name>
# It looks up a ```cmd <cmd-name> fenced block in the md file and runs its body
# with bash: stdout is the markdown result, stderr streams to the log console.
import sys, re, subprocess

def find_block(text, name):
    pat = r'(?m)^```cmd[ \t]+%s[ \t]*\n(.*?)^```' % re.escape(name)
    m = re.search(pat, text, re.S)
    return m.group(1) if m else None

def main():
    if len(sys.argv) < 3:
        sys.stderr.write('usage: demo_interp.py <path> <cmd>\n')
        return 2
    path, cmd = sys.argv[1], sys.argv[2]
    with open(path, encoding='utf-8') as f:
        body = find_block(f.read(), cmd)
    sys.stderr.write('running %r from %s\n' % (cmd, path))
    sys.stderr.flush()
    if body is None:
        sys.stderr.write('no ```cmd %s block found\n' % cmd)
        return 1
    # stderr is inherited so the command's own progress streams live to the log.
    p = subprocess.Popen(['/bin/bash', '-c', body], stdout=subprocess.PIPE, stderr=sys.stderr, text=True)
    for line in p.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    return p.wait()

if __name__ == '__main__':
    sys.exit(main())
