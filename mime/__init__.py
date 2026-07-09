"""suffix -> mime lookup: base mime.frag + ext/<name>/mime.frag by convention."""
import mimetypes
import os
import re

_MIME_RE = r'(?m)^(\.\S+)\s+(\S+)'
_HERE = os.path.dirname(__file__)
_BASE = os.path.join(_HERE, 'mime.frag')
_EXT_DIR = os.path.join(os.path.dirname(_HERE), 'ext')

_map = None

def _load():
    m = {}
    def merge(path):
        try:
            with open(path) as f:
                for suffix, mime in re.findall(_MIME_RE, f.read()):
                    m[suffix] = mime
        except OSError:
            pass
    merge(_BASE)
    if os.path.isdir(_EXT_DIR):
        for name in sorted(os.listdir(_EXT_DIR)):
            merge(os.path.join(_EXT_DIR, name, 'mime.frag'))
    return m

def guess(path):
    global _map
    if _map is None:
        _map = _load()
    for suffix, mime in _map.items():
        if path.endswith(suffix):
            return mime
    if path:
        return mimetypes.guess_type(path)[0] or 'text/plain'
    return 'text/plain'
