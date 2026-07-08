import logging
import importlib
import mimetypes

class CoreRegistry:
    def __init__(self):
        self.store_classes = {}     # 'Dir' -> DirStore
        self.mime_rules = []        # callable(path) -> mime str | None
        self.script_globals = {}    # names injected into run_script's exec namespace
        self.startup_hooks = []     # callable(context) run once at server start
        self.vmap_fragments = []    # extra vmap file paths merged into /vmap
        self.bin_dirs = []          # extra dirs prepended to PATH
        self._core_ready = False

    # -- stores --
    def register_store(self, name, cls):
        self.store_classes[name] = cls

    def get_store_cls(self, type):
        ensure_core_defaults()
        cls = self.store_classes.get(type)
        if cls is not None:
            return cls
        # Auto-load by fstab type: `T` -> stores.<t_lower>_store -> class TStore.
        # No explicit registration needed; mount types in fstab resolve directly.
        mod = importlib.import_module('stores.%s_store' % type.lower())
        cls = getattr(mod, '%sStore' % type)
        self.store_classes[type] = cls
        return cls

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
    # Core stores are auto-loaded by fstab type via get_store_cls; no explicit
    # registration needed here. Core still owns the generic mime rule.
    reg.register_mime(suffix_rule({'.svg': 'image/svg+xml'}))
