#!/usr/bin/env python3
# Cmd serve backing a mount: serves any `path/to/<file-name>` request with the
# raw file at <root>/<file-name>.
# Invoked by cmd_store.py as:  lookup.py <root> <path>
import sys, os

def main():
    if len(sys.argv) < 3:
        sys.stderr.write('usage: lookup.py <root> <path>\n')
        return 2
    root, path = sys.argv[1], sys.argv[2]
    src = os.path.join(root, os.path.basename(path))
    with open(src, 'rb') as f:
        sys.stdout.buffer.write(f.read())
    return 0

if __name__ == '__main__':
    sys.exit(main())
