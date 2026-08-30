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

def _ensure():
    global _map
    if _map is None:
        _map = _load()
    return _map

def guess_explicit(path):
    """只查 frag 显式映射表（无 mimetypes/text-plain 回退），命中返回 mime，否则 None。
    用途：缺失文件无法经 store.head 拿到 type 时，按显式映射兜底（0830-1924-ft6s）。"""
    for suffix, mime in _ensure().items():
        if path.endswith(suffix):
            return mime
    return None

def guess(path):
    for suffix, mime in _ensure().items():
        if path.endswith(suffix):
            return mime
    if path:
        return mimetypes.guess_type(path)[0] or 'text/plain'
    return 'text/plain'
