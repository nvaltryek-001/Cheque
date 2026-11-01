"""Crash-safe file writes.

A file that is written in place is half-written for a moment. If the process is killed in that moment (a redeploy
that ends in SIGKILL, an out-of-memory kill, a power cut) the file stays truncated, and a truncated model, report,
key or ledger on a persistent disk then breaks every later start. These helpers write the new content to a temporary
file in the same folder, flush it to disk, and only then rename it over the old one. A rename inside one folder is
atomic: a reader sees the old file or the new file, never a mixture, and a crash leaves the old file untouched.

Temporary files are named ".tmp-<random>" and are private (mode 600). One that a crash left behind is harmless and
is removed by `sweep` when the server starts.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, BinaryIO, Iterator

TMP_PREFIX = ".tmp-"


def _fsync_folder(folder: Path) -> None:
    """Make the rename itself durable. Not every platform allows opening a folder (Windows), so this is best effort."""
    try:
        fd = os.open(str(folder), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@contextlib.contextmanager
def open_for_write(path: str | Path) -> Iterator[BinaryIO]:
    """Yield a binary file object; on a clean exit its content replaces `path` atomically, on an error it is discarded.

        with atomic.open_for_write(target) as f:
            np.savez_compressed(f, ...)
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=TMP_PREFIX, dir=str(target.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            yield f
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    _fsync_folder(target.parent)


def write_bytes(path: str | Path, data: bytes) -> None:
    with open_for_write(path) as f:
        f.write(data)


def write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    write_bytes(path, text.encode(encoding))


def write_json(path: str | Path, obj: Any, **kwargs: Any) -> None:
    kwargs.setdefault("indent", 2)
    write_text(path, json.dumps(obj, **kwargs))


def sweep(folder: str | Path, older_than_seconds: float = 60.0) -> int:
    """Remove temporary files that a crashed write left in `folder`. Returns how many were removed."""
    root = Path(folder)
    if not root.is_dir():
        return 0
    removed = 0
    cutoff = time.time() - older_than_seconds
    for entry in root.iterdir():
        try:
            if entry.name.startswith(TMP_PREFIX) and entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed
