from subprocess import Popen, PIPE, STDOUT
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
        return Popen('%s del %s'%(self.cmd, os.path.join('/', path)), shell=True, stdout=PIPE, stderr=STDOUT).communicate()[0]
    def read(self, path):
        return Popen('%s read %s'%(self.cmd, os.path.join('/', path)), shell=True, stdout=PIPE, stderr=STDOUT).communicate()[0]
    def write(self, path, content):
        Popen('%s write %s'%(self.cmd, path), shell=True, stdin=PIPE).communicate(content)
    def __repr__(self):
        return 'Cmd("%s")'%(repr(self.cmd))

