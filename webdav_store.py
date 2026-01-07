import os
import logging
import mimetypes
import re
from webdav3.client import Client
import io

# Note: This store requires the 'webdavclient3' library.
# You can install it using: pip install webdavclient3

class WebDavStore:
    def __init__(self, hostname, username, password, root='/'):
        self.hostname = hostname
        self.root = root
        options = {
            'webdav_hostname': hostname,
            #'webdav_token': 'bmZpajpna2NieGZ6cQ==',
            'webdav_login': username,
            'webdav_password': password,
            'disable_check': True,
            'webdav_root': root
        }
        self.client = Client(options)
        self.base_dir = root

    def get_real_path(self, path):
        # The webdav client handles joining the root path
        return path
        return os.path.join(self.root, path)

    def mv(self, src, dest):
        real_src = self.get_real_path(src)
        real_dest = self.get_real_path(dest)
        self.client.move(real_src, real_dest)
        logging.info(f"Moved {real_src} to {real_dest}")
        return f'mv {real_src} {real_dest}'

    def delete(self, path):
        real_path = self.get_real_path(path)
        self.client.clean(real_path)
        logging.info(f"Deleted {real_path}")

    def head(self, path):
        real_path = self.get_real_path(path)
        try:
            info = self.client.info(real_path)
        except Exception as e:
            print(f'dav head: {real_path} {e}')
            # The client raises an exception for 404 Not Found
            return {'type': None, 'rpath': real_path}

        meta = {
            'rpath': real_path,
            'size': int(info.get('size', 0)),
            'modified': info.get('modified'),
            'created': info.get('created'),
        }

        if _path_is_dir(path) or info.get('resourcetype') == 'collection':
            meta['type'] = 'dir'
        else:
            mime_type, _ = mimetypes.guess_type(path)
            meta['type'] = mime_type or 'application/octet-stream'

        return meta

    def read_dir(self, path):
        real_path = self.get_real_path(path)
        # The webdavclient3 list() method returns full paths from the root,
        # so we need to make them relative to the current path.
        items = self.client.list(real_path)
        
        # First item is the directory itself, skip it.
        # The path from the client includes the webdav root, so we strip it.
        # We also need to handle the case where we are at the root ('/')
        
        relative_items = []
        for item in items[1:]:
            # Make path relative to the store's root, not the WebDAV server root
            if item.startswith(self.base_dir):
                item = item[len(self.base_dir):]
            relative_items.append(item)
            
        return '\n'.join(['../'] + sorted(relative_items))

    def lazy_read(self, path, range_req=''):
        real_path = self.get_real_path(path)
        
        if _path_is_dir(path):
            d = self.read_dir(path)
            return len(d), 0, len(d), [d.encode('utf-8')]
        
        # webdavclient3 doesn't directly support streaming or range requests in a simple way.
        # This implementation reads the whole file. A more advanced version would use
        # the underlying requests session to perform a ranged GET.
        if range_req:
            logging.warning("WebDavStore lazy_read does not fully support range requests. Reading entire file.")

        try:
            content = self.read(path)
            return len(content), 0, len(content), [content]
        except Exception as e:
            logging.error(f"Error reading {real_path} from WebDAV: {e}")
            return 0, 0, 0, []

    def read(self, path):
        real_path = self.get_real_path(path)
        if _path_is_dir(path):
            return self.read_dir(path).encode('utf-8')
        try:
            buffer = io.BytesIO()
            self.client.download_from(buff=buffer, remote_path=real_path)
            return buffer.getvalue()
        except Exception as e:
            logging.error(f"Error reading {real_path} from WebDAV: {e}")
            return None

    def write(self, path, content):
        real_path = self.get_real_path(path)
        try:
            if isinstance(content, str):
                content = content.encode('utf-8')
            self.client.resource(real_path).write(content)
            logging.info(f"Wrote {len(content)} bytes to {real_path}")
        except Exception as e:
            logging.error(f"Error writing to {real_path} on WebDAV: {e}")
            raise IOError from e

    def __repr__(self):
        return f'WebDavStore({self.hostname}{self.root})'
