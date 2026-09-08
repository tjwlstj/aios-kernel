"""Host operation locks; lock files are outside immutable image evidence."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat


def storage_root():
    if os.name == 'nt':
        raw = os.environ.get('LOCALAPPDATA')
        if not raw:
            raise ValueError('LOCALAPPDATA is required for local image state')
        root = Path(raw).resolve() / 'AIOS'
        if root.drive.startswith('\\\\'):
            raise ValueError('image selection state requires a local filesystem')
        return root
    return Path.home() / '.cache' / 'AIOS'


@contextmanager
def named_lock(key, *, root=None):
    """A process handle owns the lock; a leftover filename grants no ownership."""
    directory = (Path(root) if root is not None else storage_root()) / 'host-locks'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(key.encode('utf-8')).hexdigest() + '.lock')
    if path.is_symlink():
        raise ValueError('image lock must not be a symlink')
    stream = path.open('a+b')
    locked = False
    try:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('image lock must be a private regular file')
        if info.st_size == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('image operation is busy; close the active session and retry') from exc
        locked = True
        yield
    finally:
        try:
            if locked:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def image_key(directory):
    info = Path(directory).stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_ino == 0:
        raise ValueError('image directory identity is unavailable')
    # MSIX/LOCALAPPDATA and mapped-share spellings must meet at the same lock.
    return 'image-directory:%d:%d' % (info.st_dev, info.st_ino)


def image_lock(directory, *, root=None):
    return named_lock(image_key(directory), root=root)
