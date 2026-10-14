"""Close an iterable and its reservation, even when iteration never starts."""
import threading


class ClosingIterator:
    def __init__(self, iterable, on_close):
        self.iterator = iter(iterable)
        self.on_close = on_close
        self.lock = threading.Lock()
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise StopIteration
        try:
            return next(self.iterator)
        except BaseException:
            self.close()
            raise

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        try:
            close = getattr(self.iterator, 'close', None)
            if close is not None:
                close()
        finally:
            self.on_close()
