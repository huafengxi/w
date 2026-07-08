import os
from subprocess import Popen, PIPE, STDOUT
from stores.dir_store import get_mime_type, safe_read

class CmdStore(dict):
    def __init__(self, cmd):
        self.cmd = cmd
    def head(self, path):
        if os.path.isdir(os.path.join('/', path)):
            mime_type = 'dir'
        else:
            mime_type = get_mime_type(path)
        return dict(type=mime_type)
    def delete(self, path):
        raise Exception('delete disabled: %s'%(path))
    def read(self, path):
        real_path = os.path.join('/', path)
        if os.path.exists(real_path):
            return safe_read(real_path)
        return Popen('%s %s'%(self.cmd, real_path), shell=True, stdout=PIPE, stderr=STDOUT).communicate()[0]
    def write(self, path, content):
        raise Exception('write disabled: %s'%(path))
    def __repr__(self):
        return 'Cmd("%s")'%(repr(self.cmd))

