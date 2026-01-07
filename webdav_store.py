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
            'webdav_login': username,
            'webdav_password': password,
            'disable_check': True, # To allow self-signed certs, etc.
            'webdav_root': root
        }
        self.opt = options
        self.client = Client(options)
        self.base_dir = root

    def get_real_path(self, path):
        # The webdav client handles joining the root path, so the path is relative to the root.
        return path

    def mv(self, src, dest):
        real_src = self.get_real_path(src)
        real_dest = self.get_real_path(dest)
        self.client.move(real_src, real_dest, overwrite=True)
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
            # The client raises an exception for 404 Not Found
            logging.warning(f'dav head failed: {real_path} {e}')
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
        items = self.client.list(real_path)
        
        # First item is the directory itself, skip it.
        relative_items = []
        for item_path in items[1:]:
            # Make path relative to the store's root
            if item_path.startswith(self.base_dir):
                item_path = item_path[len(self.base_dir):]
            relative_items.append(item_path)
            
        return '\n'.join(['../'] + sorted(relative_items))

    def lazy_read(self, path, range_req=''):
        real_path = self.get_real_path(path)
        
        if _path_is_dir(path):
            d = self.read_dir(path)
            return len(d), 0, len(d), [d.encode('utf-8')]

        try:
            info = self.client.info(real_path)
            fsize = int(info.get('size', 0))
        except Exception as e:
            logging.error(f"Could not get info for {real_path} to perform lazy_read: {e}")
            return 0, 0, 0, []

        read_chunk_sz = 1 << 19 # 512KB

        def parse_range(range_seq, fsize):
            _ = re.findall(r'\d+', range_seq)
            if len(_) == 2:
                return int(_[0]), int(_[1]) + 1
            elif len(_) == 1:
                # This form means "from offset to the end", but we limit the chunk size for streaming.
                return int(_[0]), min(int(_[0]) + read_chunk_sz, fsize)
            else:
                return 0, fsize

        start, end = parse_range(range_req, fsize)
        
        if start >= fsize:
            return fsize, start, end, []
        
        end = min(end, fsize)

        def download_chunk_generator(start, end):
            try:
                # Use the client's session to make a streaming GET request with a Range header.
                # The session handles authentication and base URL.
                url = self.client.get_url('/' + real_path)
                headers = {'Range': f'bytes={start}-{end-1}'}
                
                with self.client.session.get(url, headers=headers, stream=True,  auth=(self.opt['webdav_login'], self.opt['webdav_password'])) as response:
                    response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=read_chunk_sz):
                        yield chunk
            except Exception as e:
                logging.error(f"Error during partial read of {real_path}: {e}")
                # On error, the generator will simply stop yielding chunks.
                return

        return fsize, start, end, download_chunk_generator(start, end)

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
            # Use upload_to which is more robust for binary data
            buffer = io.BytesIO(content)
            self.client.upload_to(buff=buffer, remote_path=real_path)
            logging.info(f"Wrote {len(content)} bytes to {real_path}")
        except Exception as e:
            logging.error(f"Error writing to {real_path} on WebDAV: {e}")
            raise IOError from e

    def __repr__(self):
        return f'WebDavStore({self.hostname}{self.root})'
