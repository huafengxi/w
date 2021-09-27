#!/bin/env python2
'''
Usages:
  ./archive.py http://127.0.0.1:8080/         # print sitemap
  ./archive.py http://127.0.0.1:8080/script
  type=tar ./archive.py http://127.0.0.1:8080 >a.tar
'''
import sys
import os
import re
import StringIO
import tarfile
import urllib2
import time
import logging

def need_ignore(path):
    return re.match('/.git|/nohup.out|/myapp.log|/emacs.d/local|/emacs.d/server|.+[.]elc', path)
def fetch(host, path, query_string):
    url = '%s/%s?%s'%(host, path, query_string)
    sleep_time = 1
    while True:
        try:
            print 'fetch url: %s'%(url)
            return urllib2.urlopen(url).read()
        except:
            print "urlopen(%s) fail, need retry"%(url)
            time.sleep(sleep_time)
            sleep_time *= 2

def join_path(p1, p2):
    return re.sub('[/]+', '/', '%s/%s'%(p1, p2))

class TarHandler:
    def __init__(self):
        self.fileobj = StringIO.StringIO()
        self.fileobj.name = 'm'
        self.tar = tarfile.TarFile(mode='w', fileobj=self.fileobj)
        self.time = time.time()
    def get_content(self):
        self.fileobj.seek(0)
        return self.fileobj.read()
    def add(self, path, content):
        path = join_path('m', path)
        if path.endswith('/'):
            self.add_dir(path)
        else:
            self.add_file(path, content)
    def add_dir(self, path):
        pass
    def add_file(self, path, content):
        fileobj = StringIO.StringIO(content)
        tarinfo = tarfile.TarInfo(name=path)
        tarinfo.size = len(content)
        tarinfo.mtime = self.time
        self.tar.addfile(tarinfo, fileobj)

def gen_sitemap(host, base='/'):
    unfetched, fetched = [base], {}
    while unfetched:
        path = unfetched.pop()
        if fetched.get(path):
            continue
        if need_ignore(path):
            continue
        if path.endswith('/'):
            content = fetch(host, path, 'v=read')
            if type(content) != 'str':
                print 'path=%s content=%s'%(path, content)
            unfetched.extend(join_path(path, line) for line in content.split('\n') if not line.startswith('..') and not line.startswith('.git'))
        fetched.update(**{path:True})
        yield path

def archive(host, base='/'):
    for path in gen_sitemap(host, base):
        logging.info('archive: %s', path)
        if path.endswith('/'):
            yield path, None
        else:
            content = fetch(host, path, 'v=read')
            yield path, content

def archive_to_tar(host, base='/'):
    handler = TarHandler()
    for path, content in archive(host, base):
        handler.add(path, content)
    return handler.get_content()
    
def help():
    print __doc__
    
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    type = os.getenv('type', 'list')
    if type == 'list':
        print '\n'.join(gen_sitemap(sys.argv[1]))
    elif type == 'tar':
        sys.stdout.write(archive_to_tar(sys.argv[1]))
    else:
        help()
            
