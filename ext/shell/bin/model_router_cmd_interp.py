#!/usr/bin/env python3
# Cmd interp bridging markdown.html inline commands to model_router's ido.
# Invoked by rpc/cmd.py as:  model_router_cmd_interp.py <dir-path> <cmd>
# It turns the dir path into ido dot-form and dispatches:
#   $model_router_dir/ido/ido.py <a.b.c>.<cmd>
# stdout is the markdown result, stderr streams to the log console.
import sys, os, subprocess

def dot_form(path):
    # e.g. .../R/output/prod_g/v1/release-regression.md -> prod_g.v1
    # keep only the dir segments under the model_router output root, dropping
    # the leading tree up to `output` and the trailing .md filename.
    parts = [p for p in path.strip('/').split('/') if p]
    if parts and parts[-1].endswith('.md'):
        parts = parts[:-1]
    if 'output' in parts:
        parts = parts[len(parts) - 1 - parts[::-1].index('output') + 1:]
    return '.'.join(parts)

def main():
    if len(sys.argv) < 3:
        sys.stderr.write('usage: model_router_cmd_interp.py <dir-path> <cmd>\n')
        return 2
    path, cmd = sys.argv[1], sys.argv[2]
    opts = sys.argv[3:]  # e.g. _fresh_, forwarded straight to ido
    root = os.environ.get('model_router_dir', '/data/yuanqi.xhf/workspace/ob_modelrouter')
    ido = os.path.join(root, 'ido', 'ido.py')
    dotted = dot_form(path)
    target = '%s.%s' % (dotted, cmd) if dotted else cmd
    sys.stderr.write('dispatching %r %s via %s\n' % (target, opts, ido))
    sys.stderr.flush()
    # stdout/stderr are inherited: ido writes straight through to cmd.py's
    # result pipe and log console, so no manual copying is needed.
    return subprocess.call([sys.executable, ido, target] + opts, cwd=root)

if __name__ == '__main__':
    sys.exit(main())
