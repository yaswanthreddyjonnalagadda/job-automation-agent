"""Process lifetime lease for a persistent Chromium profile.

Acquire before any orphan-browser cleanup, and release only after teardown.
The OS releases the lock on process death; the file is deliberately not unlinked
(unlinking an inode under a waiter would allow two owners on POSIX).
"""
from __future__ import annotations

import os
from pathlib import Path


class BrowserProfileBusy(RuntimeError):
    """Another worker owns this profile; leave its browser untouched."""


class BrowserProfileLease:
    def __init__(self, profile_dir: Path | str):
        self.profile_dir = Path(profile_dir).resolve()
        self._file = None

    def acquire(self) -> None:
        if self._file is not None:
            raise RuntimeError("Browser profile lease already acquired")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        stream = (self.profile_dir / ".jaa-worker.lock").open("a+b")
        try:
            # Windows locks a byte range. Keep a stable first byte in the file.
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise BrowserProfileBusy(
                "The browser profile is already in use or cannot be locked. "
                "Wait for its active application to finish; its browser was left untouched."
            ) from exc
        except BaseException:
            stream.close()
            raise
        self._file = stream

    def release(self) -> None:
        stream, self._file = self._file, None
        if stream is not None:
            # Closing the descriptor releases its OS lock on both platforms.
            stream.close()
