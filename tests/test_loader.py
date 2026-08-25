"""File access: the mapping's lifetime, and what teardown may not destroy."""

import os
import tempfile

import pytest

from binviz.loader import MappedFile, read_head, sha256_file

from conftest import require_sample


def test_an_error_in_the_block_survives_teardown(manifest):
    """The bug this file was added for. `mmap.close()` refuses while a
    memoryview into it is alive, and on the failure path one always is —
    the traceback pins the frame holding `mf.view`. `__exit__` then raised
    `BufferError` *over* the real exception, so `binviz signal f --name
    entropy` reported "cannot close exported pointers exist" instead of the
    KeyError listing the signals that exist. The cleanup ate the error that
    caused it.
    """
    path = require_sample("hello_static", manifest)
    with pytest.raises(KeyError, match="unknown signals"):
        with MappedFile.open(path) as mf:
            view = mf.view          # noqa: F841 — the export is the point
            raise KeyError("unknown signals ['entropy']")


def test_closing_with_a_live_view_does_not_raise(manifest):
    """Same rule on the success path, called explicitly rather than through
    `__exit__`."""
    path = require_sample("hello_static", manifest)
    mf = MappedFile.open(path)
    view = mf.view
    mf.close()
    # Deferred, not leaked: the mapping outlives close() and is unmapped
    # when the last exporter goes, so the view is still readable here.
    assert bytes(view[:4]) == b"\x7fELF"


def test_a_clean_close_still_closes(manifest):
    """The tolerance must not turn into never closing anything."""
    path = require_sample("hello_static", manifest)
    with MappedFile.open(path) as mf:
        assert mf.size > 0
    assert mf._mmap is None and mf._file is None


def test_an_empty_file_degrades_to_a_zero_length_view():
    """mmap of an empty file raises, so `open` skips the mapping — and the
    close path must cope with there being nothing to close."""
    fd, tmp = tempfile.mkstemp()
    os.close(fd)
    try:
        with MappedFile.open(tmp) as mf:
            assert mf.size == 0
            assert bytes(mf.view) == b""
    finally:
        os.unlink(tmp)


def test_hashing_and_head_reads_agree_with_the_mapping(manifest):
    """`sha256_file` and `read_head` open the file themselves rather than
    going through the mapping; they must still describe the same bytes."""
    path = require_sample("hello_static", manifest)
    with MappedFile.open(path) as mf:
        head = bytes(mf.view[:64])
        size = mf.size
    assert read_head(path, 64) == head
    assert len(sha256_file(path)) == 64
    assert os.path.getsize(path) == size
