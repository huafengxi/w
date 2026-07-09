from stores.dict_store import DictStore
from vmap import build

class VmapStore(DictStore):
    def __init__(self):
        DictStore.__init__(self)
        for k, v in build().items():
            self.write(k, v)
