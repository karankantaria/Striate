"""File access: mmap, hashing, chunked reads.

The single place that touches the filesystem for binary content. Everything
downstream works on memoryviews handed out from here.
"""

from __future__ import annotations

import hashlib
import mmap
import os
from dataclasses import dataclass

_CHUNK = 1 << 20  # 1 MiB


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def read_head(path: str | os.PathLike, n: int = 64) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


@dataclass
class MappedFile:
    """An open, memory-mapped binary. Use as a context manager."""

    path: str
    size: int
    _file: object
    _mmap: mmap.mmap | None

    @classmethod
    def open(cls, path: str | os.PathLike) -> "MappedFile":
        f = open(path, "rb")
        size = os.fstat(f.fileno()).st_size
        # mmap of an empty file raises; degrade to a zero-length view
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) if size else None
        return cls(path=os.fspath(path), size=size, _file=f, _mmap=mm)

    @property
    def view(self) -> memoryview:
        if self._mmap is None:
            return memoryview(b"")
        return memoryview(self._mmap)

    def close(self) -> None:
        if self._mmap is not None:
            self._mmap.close()
            self._mmap = None
        if self._file is not None:
            self._file.close()  # type: ignore[attr-defined]
            self._file = None

    def __enter__(self) -> "MappedFile":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
