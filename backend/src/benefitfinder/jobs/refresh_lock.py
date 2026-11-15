"""Cross-platform nonblocking lock for F08 refresh files."""

import os

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def acquire_refresh_lock(descriptor: int) -> None:
    """Hold the exclusive lock until the caller closes the descriptor."""
    if os.name == "nt":
        # Windows locks a byte range from the current file position.
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
