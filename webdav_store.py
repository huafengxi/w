import os
import logging
import mimetypes
import re
import io
from webdav4.client import Client

# Note: This store requires the 'webdav4' library.
# You can install it using: pip install webdav4

#def _path_is_dir(path):
#    return path.endswith('/')

class WebDavStore:
    def __init__(self, hostname, username, password, root='/', verify=False):
        self.hostname = hostname.rstrip('/')
        self.root = '/' + root.strip('/')
        
        # In webdav4, we include the root in the base_url
        base_url = f"{self.hostname}{self.root}"
        
        self.client = Client(
            base_url, 
            auth=(username, password), 
            verify=verify  # False replaces 'disable_check'
        )

    def get_real_path(self, path):
        # webdav4 handles paths relative to base_url provided in __init__
        return path.lstrip('/')

    def mv(self, src, dest):
        real_src = self.get_real_path(src)
        real_dest = self.get_real_path(dest)
        self.client.move(real_src, real_dest, overwrite=True)
        logging.info(f"Moved {real_src} to {real_dest}")
        return f'mv {real_src} {real_dest}'

    def delete(self, path):
        real_path = self.get_real_path(path)
        self.client.remove(real_path)
        logging.info(f"Deleted {real_path}")

    def head(self, path):
        real_path = self.get_real_path(path)
        try:
            info = self.client.info(real_path)
            # info keys: 'name', 'size', 'created', 'modified', 'content_type', 'etag', 'isdir'
            
            meta = {
                'rpath': real_path,
                'size': info.get('size', 0),
                'modified': info.get('modified'),
                'created': info.get('created'),
            }

            if info.get('isdir') or _path_is_dir(path):
                meta['type'] = 'dir'
            else:
                meta['type'] = info.get('content_type') or mimetypes.guess_type(path)[0] or 'application/octet-stream'

            return meta
        except Exception as e:
            logging.warning(f'dav head failed: {real_path} {e}')
            return {'type': None, 'rpath': real_path}

    def read_dir(self, path):
        real_path = self.get_real_path(path)

        # detail=True returns a list of dicts with 'name', 'isdir', etc.
        # These names are already relative to the 'real_path' (basenames).
        try:
            items = self.client.ls(real_path, detail=True)
        except Exception as e:
            logging.error(f"Error listing directory {real_path}: {e}")
            return '../'

        relative_items = []
        for item in items:
            name = item['name']
            # Append '/' if the item is a directory
            is_collection = (
                item.get('isdir') or
                item.get('href', '').endswith('/') or
                item.get('content_type') in ('httpd/unix-directory', 'directory')
            )
            name = os.path.split(name)[1]
            if is_collection:
                name += '/'
            relative_items.append(name)

        # Return sorted list with '../' prepended as per original script logic
        return '\n'.join(['../'] + sorted(relative_items))

    def lazy_read(self, path, range_req=''):
        real_path = self.get_real_path(path)
        
        if _path_is_dir(path):
            d = self.read_dir(path)
            return len(d), 0, len(d), [d.encode('utf-8')]

        try:
            info = self.client.info(real_path)
            fsize = info.get('size', 0)
        except Exception as e:
            logging.error(f"Could not get info for {real_path}: {e}")
            return 0, 0, 0, []

        read_chunk_sz = 1 << 22 # 4MB

        def parse_range(range_seq, fsize):
            nums = re.findall(r'\d+', range_seq)
            if len(nums) == 2:
                return int(nums[0]), int(nums[1]) + 1
            elif len(nums) == 1:
                return int(nums[0]), min(int(nums[0]) + read_chunk_sz, fsize)
            else:
                return 0, fsize

        start, end = parse_range(range_req, fsize)
        if start >= fsize:
            return fsize, start, end, []
        
        end = min(end, fsize)

        def download_chunk_generator(start, end):
            try:
                # webdav4 uses httpx internally. We use the internal http client
                # to perform a range request while keeping auth/session info.
                headers = {'Range': f'bytes={start}-{end-1}'}
                
                # Note: path in httpx.get needs to be relative to base_url
                with self.client.http.stream("GET", real_path, headers=headers) as r:
                    r.raise_for_status()
                    for chunk in r.iter_bytes(chunk_size=4096):
                        yield chunk
            except Exception as e:
                logging.error(f"Error during partial read of {real_path}: {e}")
                return

        return fsize, start, end, download_chunk_generator(start, end)

    def read(self, path):
        real_path = self.get_real_path(path)
        if _path_is_dir(path):
            return self.read_dir(path).encode('utf-8')
        try:
            buffer = io.BytesIO()
            # upload_fileobj/download_fileobj are the standard for buffer ops in webdav4
            self.client.download_fileobj(real_path, buffer)
            return buffer.getvalue()
        except Exception as e:
            logging.error(f"Error reading {real_path} from WebDAV: {e}")
            return None

    def write(self, path, content):
        real_path = self.get_real_path(path)
        try:
            if isinstance(content, str):
                content = content.encode('utf-8')
            
            buffer = io.BytesIO(content)
            self.client.upload_fileobj(buffer, real_path, overwrite=True)
            logging.info(f"Wrote {len(content)} bytes to {real_path}")
        except Exception as e:
            logging.error(f"Error writing to {real_path} on WebDAV: {e}")
            raise IOError from e

    def __repr__(self):
        return f'WebDavStore({self.hostname}{self.root})'
