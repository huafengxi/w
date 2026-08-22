import os
import re
import logging
import mimetypes
import io
import json
from urllib.parse import urlsplit, unquote
from webdav4.client import Client
from stores.store import _path_is_dir

def _resolve_env_path(name):
    if os.path.isabs(name) and os.path.isfile(name):
        return name
    cand = os.path.join(os.getcwd(), name)
    if os.path.isfile(cand):
        return cand
    cand = os.path.join(os.path.dirname(os.path.realpath(__file__)), name)
    if os.path.isfile(cand):
        return cand
    return name  # let the open() downstream raise the real error

def _parse_env_file(path):
    env = {}
    with open(path) as f:
        for line in f:
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_]\w*)\s*=\s*(.*)\s*$", line)
            if not m:
                continue
            val = m.group(2).strip()
            if (len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'")):
                val = val[1:-1]
            env[m.group(1)] = val
    return env

def _creds_from_env(env):
    url = env.get('WEBDAV_ENDPOINT_URL') or env.get('WEBDAV_URL')
    if url:
        p = urlsplit(url)
        hostname = '://'.join([p.scheme, (p.hostname or '')])
        if p.port:
            hostname = '%s:%d' % (hostname, p.port)
        return (
            hostname,
            unquote(p.username) if p.username else env.get('WEBDAV_USERNAME', ''),
            unquote(p.password) if p.password else env.get('WEBDAV_PASSWORD', ''),
            env.get('WEBDAV_ROOT', '/'),
            _str_bool(env.get('WEBDAV_VERIFY', '0')),
        )
    return (
        env.get('WEBDAV_HOSTNAME', ''),
        env.get('WEBDAV_USERNAME', ''),
        env.get('WEBDAV_PASSWORD', ''),
        env.get('WEBDAV_ROOT', '/'),
        _str_bool(env.get('WEBDAV_VERIFY', '0')),
    )

def _str_bool(s):
    return str(s).lower() in ('1', 'true', 'yes', 'on')

class AlistClient:
    """Thin wrapper around alist HTTP API for cached read operations."""
    def __init__(self, base_url, username='admin', password='admin'):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self._token = None
        import urllib.request
        # Bypass proxy for localhost
        proxy_handler = urllib.request.ProxyHandler({})
        self._opener = urllib.request.build_opener(proxy_handler)

    def _ensure_token(self):
        if self._token:
            return self._token
        try:
            import urllib.request
            data = json.dumps({'username': self.username, 'password': self.password}).encode()
            req = urllib.request.Request(
                f'{self.base_url}/api/auth/login',
                data=data,
                headers={'Content-Type': 'application/json'}
            )
            resp = self._opener.open(req, timeout=10)
            result = json.loads(resp.read())
            self._token = result['data']['token']
        except Exception as e:
            logging.warning(f'alist login failed: {e}')
        return self._token

    def _api(self, method, path, body=None):
        import urllib.request
        token = self._ensure_token()
        if not token:
            return None
        url = f'{self.base_url}{path}'
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(
            url, data=data,
            headers={
                'Content-Type': 'application/json',
                'Authorization': token,
            }
        )
        req.get_method = lambda: method
        try:
            resp = self._opener.open(req, timeout=30)
            return json.loads(resp.read())
        except Exception as e:
            logging.warning(f'alist api {method} {path} failed: {e}')
            return None

    def list_dir(self, alist_path, per_page=200):
        """List a directory via alist API. Returns list of items with name, size, is_dir."""
        result = self._api('POST', '/api/fs/list', {
            'path': alist_path,
            'password': '',
            'page': 1,
            'per_page': per_page,
            'refresh': False,
        })
        if result and result.get('code') == 200:
            return result['data']['content']
        return None

    def get_info(self, alist_path):
        """Get file/dir info via alist API."""
        result = self._api('POST', '/api/fs/get', {
            'path': alist_path,
            'password': '',
        })
        if result and result.get('code') == 200:
            return result['data']
        return None

    def raw_download(self, alist_path, range_header=None):
        """Download raw file via alist /d/ endpoint."""
        import urllib.request
        token = self._ensure_token()
        if not token:
            return None
        url = f'{self.base_url}/d{"" if alist_path.startswith("/") else "/"}{alist_path}'
        headers = {'Authorization': token}
        if range_header:
            headers['Range'] = range_header
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = self._opener.open(req, timeout=60)
            return resp
        except Exception as e:
            logging.warning(f'alist download {alist_path} failed: {e}')
            return None


class WebDavStore:
    def __init__(self, hostname, username=None, password=None, root='/', verify=False,
                 alist_base_url=None, alist_mount_path=None):
        # Parse env file if hostname is a .env file
        env = {}
        if username is None and isinstance(hostname, str) and hostname.endswith('.env'):
            env = _parse_env_file(_resolve_env_path(hostname))
            hostname, username, password, root, verify = _creds_from_env(env)
            if not alist_base_url:
                alist_base_url = env.get('ALIST_BASE_URL')
            if not alist_mount_path:
                alist_mount_path = env.get('ALIST_MOUNT_PATH')

        self.hostname = hostname.rstrip('/')
        self.root = '/' + root.strip('/')
        
        # In webdav4, we include the root in the base_url
        base_url = f"{self.hostname}{self.root}"
        
        self.client = Client(
            base_url, 
            auth=(username, password), 
            verify=verify,
            follow_redirects=True,
            timeout=60.0 
        )

        # Alist proxy for faster reads
        self.alist = None
        self.alist_mount_path = alist_mount_path
        if alist_base_url:
            alist_user = env.get('ALIST_USERNAME', 'admin')
            alist_pass = env.get('ALIST_PASSWORD', 'admin')
            self.alist = AlistClient(alist_base_url, alist_user, alist_pass)
            logging.info(f'alist proxy enabled: {alist_base_url} mount={alist_mount_path}')

    def _alist_path(self, path):
        """Convert a store path to an alist path."""
        if not self.alist_mount_path:
            return path
        p = path.lstrip('/')
        mp = self.alist_mount_path.rstrip('/')
        return f'{mp}/{p}' if p else mp

    def get_real_path(self, path):
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

    def mkdir(self, path):
        real_path = self.get_real_path(path)
        self.client.mkdir(real_path)
        logging.info(f"Mkdir {real_path}")

    def _alist_type_to_mime(self, type_val, path):
        """Convert alist numeric type to MIME string."""
        if isinstance(type_val, str):
            return type_val
        # alist PikPak driver returns numeric type: 1=folder, 2=video, 3=image, 4=audio, 5=doc, 6=archive
        if isinstance(type_val, int):
            if type_val == 1:
                return 'dir'
            # Try mimetypes first, fall back to generic
            mime = mimetypes.guess_type(path)[0]
            if mime:
                return mime
            type_map = {2: 'video/mp4', 3: 'image/jpeg', 4: 'audio/mpeg', 5: 'application/octet-stream', 6: 'application/zip'}
            return type_map.get(type_val, 'application/octet-stream')
        return mimetypes.guess_type(path)[0] or 'application/octet-stream'

    def head(self, path):
        real_path = self.get_real_path(path)

        # Try alist first for fast cached metadata
        if self.alist:
            info = self.alist.get_info(self._alist_path(path))
            if info:
                meta = {
                    'rpath': real_path,
                    'size': info.get('size', 0),
                    'modified': info.get('modified'),
                    'created': info.get('created'),
                }
                if info.get('is_dir') or _path_is_dir(path):
                    meta['type'] = 'dir'
                else:
                    meta['type'] = self._alist_type_to_mime(info.get('type'), path)
                return meta

        # Fallback to direct WebDAV
        try:
            info = self.client.info(real_path)
            meta = {
                'rpath': real_path,
                'size': info.get('content_length', 0),
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

        # Try alist first for fast cached listing
        if self.alist:
            items = self.alist.list_dir(self._alist_path(path))
            if items is not None:
                relative_items = []
                for item in items:
                    name = item['name']
                    if item.get('is_dir'):
                        name += '/'
                    relative_items.append(name)
                return '\n'.join(['../'] + sorted(relative_items))

        # Fallback to direct WebDAV
        try:
            items = self.client.ls(real_path, detail=True)
        except Exception as e:
            logging.error(f"Error listing directory {real_path}: {e}")
            return '../'

        relative_items = []
        for item in items:
            name = item['name']
            is_collection = (
                item.get('isdir') or
                item.get('href', '').endswith('/') or
                item.get('content_type') in ('httpd/unix-directory', 'directory')
            )
            name = os.path.split(name)[1]
            if is_collection:
                name += '/'
            relative_items.append(name)

        return '\n'.join(['../'] + sorted(relative_items))

    def lazy_read(self, path, range_req=''):
        real_path = self.get_real_path(path)
        
        if _path_is_dir(path):
            d = self.read_dir(path)
            return len(d), 0, len(d), [d.encode('utf-8')]

        # Try alist for fast cached download
        if self.alist and not range_req:
            resp = self.alist.raw_download(self._alist_path(path))
            if resp:
                data = resp.read()
                return len(data), 0, len(data), [data]

        try:
            info = self.client.info(real_path)
            fsize = info.get('content_length', 0)
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
                headers = {'Range': f'bytes={start}-{end-1}'}
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

        # Try alist for fast cached download
        if self.alist:
            resp = self.alist.raw_download(self._alist_path(path))
            if resp:
                return resp.read()

        try:
            buffer = io.BytesIO()
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
        s = f'WebDavStore({self.hostname}{self.root})'
        if self.alist:
            s += f' [alist: {self.alist.base_url}/{self.alist_mount_path}]'
        return s