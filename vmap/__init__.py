"""vmap composer: base vmap.frag + ext/<name>/vmap.frag by convention."""
import os
import re

_VMAP_RE = r'(?m)^([^# ]+):\s+(\S*)\n'
_HERE = os.path.dirname(__file__)
_BASE = os.path.join(_HERE, 'vmap.frag')
_EXT_DIR = os.path.join(os.path.dirname(_HERE), 'ext')

def _read(path):
    try:
        with open(path) as f:
            return re.findall(_VMAP_RE, f.read())
    except OSError:
        return []

def build():
    pairs = _read(_BASE)
    if os.path.isdir(_EXT_DIR):
        for name in sorted(os.listdir(_EXT_DIR)):
            pairs.extend(_read(os.path.join(_EXT_DIR, name, 'vmap.frag')))
    return dict(pairs)
