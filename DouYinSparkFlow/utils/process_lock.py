"""Advisory locks shared by containers, independent of their PID namespaces."""
import errno
try:
    import fcntl
except ImportError:  # Native Windows retains its local PID lock implementation.
    fcntl = None

SUPPORTED = fcntl is not None


def try_lock(handle):
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EACCES):
            return False
        raise


def unlock(handle):
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def is_locked(path):
    if not SUPPORTED:
        return None
    try:
        with path.open('r+') as handle:
            if not try_lock(handle):
                return True
            unlock(handle)
            return False
    except FileNotFoundError:
        return False
    except OSError:
        return None
