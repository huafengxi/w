import logging
import mimetypes

class CoreRegistry:
    def __init__(self):
        self.mime_rules = []        # callable(path) -> mime str | None
        self.find_presets = {}      # {shorthand: regex} for core find?t= handler
        self.vmap_fragments = []    # extra vmap file paths merged into /vmap
        self.bin_dirs = []          # extra dirs prepended to PATH
        self._core_ready = False

    # -- mime --
    def register_mime(self, rule):
        self.mime_rules.append(rule)

    def guess_mime(self, path):
        ensure_core_defaults()
        for rule in self.mime_rules:
            t = rule(path)
            if t:
                return t
        if path:
            return mimetypes.guess_type(path)[0] or 'text/plain'
        return 'text/plain'

REGISTRY = CoreRegistry()

def suffix_rule(mapping):
    """Build a mime rule from a {suffix: mime} mapping."""
    def rule(path):
        for suffix, mime in mapping.items():
            if path.endswith(suffix):
                return mime
        return None
    return rule

def ensure_core_defaults():
    reg = REGISTRY
    if reg._core_ready:
        return
    reg._core_ready = True  # set first to avoid re-entrancy during imports
    reg.register_mime(suffix_rule({'.svg': 'image/svg+xml'}))
