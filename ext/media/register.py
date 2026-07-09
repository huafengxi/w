# content-type presets for the core `find?t=` shorthand; media owns this knowledge.
_FIND_PRESETS = dict(
    audio='[.]mp3$|[.]flac$|[.]m4a$|[.]wav$|[.]ogg$', video='[.]mp4',
    text='[.]txt$', img='[.]png$|[.]jpg$|[.]gif$', plist='[.]plist$')

def register(reg):
    reg.find_presets.update(_FIND_PRESETS)
