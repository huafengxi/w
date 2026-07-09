# -*- type=script -*-
# `v=find` — content-type presets (t=audio/video/...) live here; media owns this.
_PRESETS = dict(
    audio='[.]mp3$|[.]flac$|[.]m4a$|[.]wav$|[.]ogg$', video='[.]mp4',
    text='[.]txt$', img='[.]png$|[.]jpg$|[.]gif$', plist='[.]plist$')

def interp(store, src=None, **kw):
    pat = kw.get('pat') or _PRESETS.get(kw.get('t'), '')
    li = [i for i in store.find(src) if re.search(pat, i, re.IGNORECASE)]
    return dict(type='text/plain'), '\n'.join(li)
