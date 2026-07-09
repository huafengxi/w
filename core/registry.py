class CoreRegistry:
    def __init__(self):
        self.find_presets = {}      # {shorthand: regex} for core find?t= handler
        self.bin_dirs = []          # extra dirs prepended to PATH

REGISTRY = CoreRegistry()
