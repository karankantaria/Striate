"""Unit tests for header-magic sniffing — no corpus required."""

import json
import struct

from binviz.cli import main
from binviz.probe import guess_format, probe


def test_elf():
    assert guess_format(b"\x7fELF" + b"\x00" * 12) == "elf"


def test_macho_variants():
    for magic in (b"\xfe\xed\xfa\xce", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
        assert guess_format(magic + b"\x00" * 12) == "macho"


def test_png():
    assert guess_format(b"\x89PNG\r\n\x1a\n") == "png"


def test_zip():
    assert guess_format(b"PK\x03\x04rest") == "zip"


def test_gzip():
    assert guess_format(b"\x1f\x8b\x08") == "gzip"


def test_empty_and_raw():
    assert guess_format(b"") == "raw"
    assert guess_format(b"hello there") == "raw"


def test_mz_without_pe_is_dos(tmp_path):
    # MZ stub whose e_lfanew points at garbage
    head = bytearray(b"MZ" + b"\x00" * 62)
    struct.pack_into("<I", head, 0x3C, 0x40)
    p = tmp_path / "dos.exe"
    p.write_bytes(bytes(head) + b"NOPE")
    assert probe(p)["guessed_format"] == "dos"


def test_mz_with_pe_signature(tmp_path):
    head = bytearray(b"MZ" + b"\x00" * 62)
    struct.pack_into("<I", head, 0x3C, 0x80)
    p = tmp_path / "win.exe"
    p.write_bytes(bytes(head) + b"\x00" * (0x80 - 0x40) + b"PE\x00\x00" + b"\x00" * 20)
    assert probe(p)["guessed_format"] == "pe"


def test_probe_fields(tmp_path):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"\x00" * 100)
    result = probe(p)
    assert result["size"] == 100
    assert len(result["sha256"]) == 64
    assert result["magic"] == "00" * 8
    assert result["guessed_format"] == "raw"


def test_probe_empty_file(tmp_path):
    p = tmp_path / "empty"
    p.write_bytes(b"")
    result = probe(p)
    assert result["size"] == 0
    assert result["guessed_format"] == "raw"


def test_cli_probe(tmp_path, capsys):
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x7fELF" + b"\x00" * 60)
    assert main(["probe", str(p)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["guessed_format"] == "elf"
    assert out["size"] == 64


def test_cli_probe_missing_file(tmp_path, capsys):
    assert main(["probe", str(tmp_path / "nope")]) == 1
    assert "cannot read" in capsys.readouterr().err
