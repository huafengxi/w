import logging
import os
import sys
import subprocess

from core.registry import suffix_rule
from ext.media.album import AlbumDB

# content-type presets for the core `find?t=` shorthand; media owns this knowledge.
_FIND_PRESETS = dict(
    audio='[.]mp3$|[.]flac$|[.]m4a$|[.]wav$|[.]ogg$', video='[.]mp4',
    text='[.]txt$', img='[.]png$|[.]jpg$|[.]gif$', plist='[.]plist$')

def _start_timestamp_server(ctx):
    script = ctx['web_path']('w/ext/media/timestamp-server.py')
    if not os.path.exists(script):
        logging.warning('timestamp-server.py not found at %s', script)
        return
    try:
        subprocess.call(['pkill', '-f', 'timestamp-server.py'])
    except Exception:
        pass
    logging.info('launching timestamp-server.py')
    subprocess.Popen([sys.executable, script])

def register(reg):
    reg.script_globals['AlbumDB'] = AlbumDB
    reg.script_globals['find_presets'] = _FIND_PRESETS
    reg.register_mime(suffix_rule({'.plist': 'plist', '.flac': 'audio/flac'}))
    reg.startup_hooks.append(_start_timestamp_server)
    reg.vmap_fragments.append('w/ext/media/vmap.frag')
