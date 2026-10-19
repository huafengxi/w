"""Caller-owned filesystem configuration; no deployment defaults."""
import os


def path(name, required=False):
    value = os.environ.get(name, '')
    if not value:
        if required:
            raise ValueError('%s is required' % name)
        return None
    value = os.path.expanduser(value)
    if not os.path.isabs(value):
        raise ValueError('%s must be an absolute path' % name)
    return value


def workspace():
    value = path('W_WORKSPACE', required=True)
    if not os.path.isdir(value):
        raise ValueError('W_WORKSPACE must be an existing directory')
    return os.path.realpath(value)
