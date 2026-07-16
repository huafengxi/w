#!/usr/bin/env python3
# Cmd serve backing the /R mount: serves any `path/to/<file-name>.md` request
# with the raw template at $model_router_dir/R/cases/<file-name>.md.
# Invoked by cmd_store.py as:  model_router_md_template_serve.py <path>
import sys, os

def main():
    if len(sys.argv) < 2:
        sys.stderr.write('usage: model_router_md_template_serve.py <path>\n')
        return 2
    name = os.path.basename(sys.argv[1])
    root = os.environ.get('model_router_dir', '/data/yuanqi.xhf/workspace/ob_modelrouter')
    src = os.path.join(root, 'R', 'cases', name)
    with open(src, 'rb') as f:
        sys.stdout.buffer.write(f.read())
    return 0

if __name__ == '__main__':
    sys.exit(main())
