import os
from subprocess import Popen, PIPE, STDOUT
from stores.dir_store import get_mime_type, safe_read

class CmdStore(dict):
    def __init__(self, *cmd):
        self.cmd = ' '.join(cmd)
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
            # safe_read 把 open 的 OSError（= IOError）吞成 None（目录的
            # IsADirectoryError、无权限文件的 PermissionError 等），None 流到消费方
            # （core/rpc/core_read.py 的 store.read(src).split(...) 等）即 500。
            # 收口在 store 边界：返回与本 store 其余分支（safe_read / Popen stdout）
            # 同类型的空值 bytes。目录路径不走这里 —— 由下面的 read_dir 承接。
            data = safe_read(real_path)
            return b'' if data is None else data
        return Popen('%s %s'%(self.cmd, real_path), shell=True, stdout=PIPE, stderr=STDOUT).communicate()[0]
    def read_dir(self, path):
        # 目录列表接口（RootStore.read/find 对 dir 路径优先走它）：返回 str，与
        # DirStore.read_dir 同款形态 —— 消费方按 '\n' 切条目。
        real_path = os.path.join('/', path)
        if not os.path.isdir(real_path):
            return ''
        items = sorted(os.listdir(real_path))
        return '\n'.join(['../'] + [name + ['', '/'][os.path.isdir(os.path.join(real_path, name))] for name in items])
    def write(self, path, content):
        raise Exception('write disabled: %s'%(path))
    def __repr__(self):
        return 'Cmd("%s")'%(repr(self.cmd))

