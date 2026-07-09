class CoreRegistry:
    def __init__(self):
        self.find_presets = {}      # {shorthand: regex} for core find?t= handler

REGISTRY = CoreRegistry()
