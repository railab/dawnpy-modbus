# tools/dawnpy-modbus/tests/test_files.py
#
# SPDX-License-Identifier: Apache-2.0
#

"""Exercise standard file records and the existing seekable console API."""

from types import SimpleNamespace

import pytest
from pymodbus.pdu.file_message import FileRecord

from dawnpy_modbus.client import ModbusClient
from dawnpy_modbus.console import ModbusConsole
from dawnpy_modbus.files import SeekableFile


class _MemoryFileClient:
    def __init__(self, data=b""):
        self.data = bytearray(data)
        self.calls = []

    def read_file_records(self, unit, number, record, count):
        self.calls.append(("read", number, record, count))
        assert count <= 121
        assert record + count <= 10000
        offset = (number - 1) * 20000 + record * 2
        return bytes(self.data[offset : offset + count * 2]).ljust(
            count * 2, b"\0"
        )

    def write_file_records(self, unit, number, record, data):
        self.calls.append(("write", number, record, data))
        assert len(data) <= 240
        assert record + len(data) // 2 <= 10000
        offset = (number - 1) * 20000 + record * 2
        if offset == 0:
            self.data.clear()
        if offset > len(self.data):
            self.data.extend(bytes(offset - len(self.data)))
        self.data[offset : offset + len(data)] = data


@pytest.fixture
def console(monkeypatch):
    instance = ModbusConsole.__new__(ModbusConsole)
    instance.unit = 1
    instance._seekable_offsets = {}
    monkeypatch.setattr(instance, "warn", lambda msg: pytest.fail(msg))
    return instance


@pytest.mark.parametrize(
    "size", [0, 2, 238, 240, 242, 244, 20000, 20002, 40000]
)
def test_roundtrip(size):
    client = _MemoryFileClient()
    file = SeekableFile(client, 1, 1, 2)
    data = bytes(i % 251 for i in range(size))
    file.write(0, data)
    assert file.read(0, size) == data
    assert file.read(0, size) == data
    assert bytes(client.data) == data
    if data:
        assert client.calls[0][1:3] == (1, 0)


@pytest.mark.parametrize("number,count", [(0, 1), (1, 0), (65535, 2)])
def test_bad_mapping(number, count):
    with pytest.raises(ValueError, match="range"):
        SeekableFile(_MemoryFileClient(), 1, number, count)


def test_last_file_is_valid():
    file = SeekableFile(_MemoryFileClient(), 1, 65535, 1)
    assert file.number == 65535


@pytest.mark.parametrize(
    "offset,size", [(1, 243), (19999, 5), (20001, 500), (39999, 1)]
)
def test_byte_offsets(offset, size):
    data = bytes(i % 251 for i in range(40000))
    file = SeekableFile(_MemoryFileClient(data), 1, 1, 2)
    assert file.read(offset, size) == data[offset : offset + size]
    assert file.read(offset, size) == data[offset : offset + size]


def test_reads_pad_eof_and_accept_empty_io():
    file = SeekableFile(_MemoryFileClient(b"abc"), 1, 1, 2)
    assert file.read(0, 8) == b"abc" + bytes(5)
    assert file.read(20000, 2) == bytes(2)
    assert SeekableFile(_MemoryFileClient(), 1, 1, 1).read(0, 4) == bytes(4)


@pytest.mark.parametrize("offset,size", [(-1, 2), (0, -1), (39999, 2)])
def test_invalid_read_range(offset, size):
    client = _MemoryFileClient()
    with pytest.raises(ValueError, match="allocation"):
        SeekableFile(client, 1, 1, 2).read(offset, size)
    assert not client.calls


@pytest.mark.parametrize("offset,data", [(1, b"ab"), (0, b"a")])
def test_unaligned_write(offset, data):
    client = _MemoryFileClient()
    with pytest.raises(ValueError, match="alignment"):
        SeekableFile(client, 1, 1, 2).write(offset, data)
    assert not client.calls


def test_write_range_checked_before_sending():
    client = _MemoryFileClient()
    with pytest.raises(ValueError, match="allocation"):
        SeekableFile(client, 1, 1, 1).write(0, bytes(20002))
    assert not client.calls


def test_nonzero_write_crosses_file_boundary():
    client = _MemoryFileClient(bytes(20004))
    file = SeekableFile(client, 1, 1, 2)
    file.write(19998, b"abcdef")
    assert file.read(19996, 8) == b"\0\0abcdef"
    assert client.calls[:2] == [
        ("write", 1, 9999, b"ab"),
        ("write", 2, 0, b"cdef"),
    ]


def test_write_at_zero_retains_replacement_semantics():
    client = _MemoryFileClient(b"old contents")
    file = SeekableFile(client, 1, 1, 1)
    file.write(0, b"ab")
    assert bytes(client.data) == b"ab"
    file.write(0, b"")
    assert bytes(client.data) == b"ab"  # No empty-file command in FC21.


def test_short_read():
    client = SimpleNamespace(read_file_records=lambda *args: b"a")
    with pytest.raises(OSError, match="truncated"):
        SeekableFile(client, 1, 1, 1).read(0, 2)


def test_write_failure_propagates():
    def fail(*args):
        raise OSError("link lost")

    client = SimpleNamespace(write_file_records=fail)
    with pytest.raises(OSError, match="link lost"):
        SeekableFile(client, 1, 1, 1).write(0, b"ab")


def test_console_seekable_scalar_write_keeps_little_endian_payload(console):
    client = _MemoryFileClient()
    binding = SimpleNamespace(
        group_type="seekable",
        start_address=0,
        register_count=3,
        dtype="uint16",
    )
    assert console._write_binding(client, binding, 0x1234)
    assert bytes(client.data) == b"\x34\x12"
    assert client.calls == [("write", 1, 0, b"\x34\x12")]


def test_record_wrapper_uses_bytes_and_checks_echo(monkeypatch):
    client = ModbusClient("unused")
    calls = []

    def call(method, records, **kwargs):
        calls.append((method, records, kwargs))
        if method == "read_file_record":
            assert records[0].record_length == 2
            return SimpleNamespace(records=[FileRecord(record_data=b"abcd")])
        return SimpleNamespace(records=records)

    monkeypatch.setattr(client, "_call", call)
    assert client.read_file_records(3, 7, 2, 2) == b"abcd"
    client.write_file_records(3, 7, 2, b"\x12\x34")
    assert calls[1][1][0].record_data == b"\x12\x34"
    assert calls[1][2] == {"device_id": 3}
    monkeypatch.setattr(client, "_call", lambda *a, **kw: None)
    with pytest.raises(OSError):
        client.read_file_records(3, 7, 2, 2)
    with pytest.raises(OSError):
        client.write_file_records(3, 7, 2, b"ab")


def test_console_read_then_write_preserves_cursor(console):
    client = _MemoryFileClient(b"abcdefgh")
    binding = SimpleNamespace(
        group_type="seekable",
        start_address=0,
        register_count=3,
        dtype="uint16",
    )
    result = console._read_seekable_binding(client, binding)
    assert result is not None
    assert result[0] == [
        0x6261,
        0x6463,
    ]
    assert console._write_binding(client, binding, 0x1234)
    assert bytes(client.data) == b"abcd\x34\x12gh"
    result = console._read_seekable_binding(client, binding)
    assert result is not None
    assert result[0] == [
        0x1234,
        0x6867,
    ]


def test_console_failed_read_keeps_cursor(console, monkeypatch):
    client = _MemoryFileClient(b"abcd")
    binding = SimpleNamespace(
        group_type="seekable", start_address=0, register_count=2
    )
    warnings = []
    monkeypatch.setattr(console, "warn", warnings.append)
    read = client.read_file_records
    monkeypatch.setattr(client, "read_file_records", lambda *args: b"")
    assert console._read_seekable_binding(client, binding) is None
    monkeypatch.setattr(client, "read_file_records", read)
    result = console._read_seekable_binding(client, binding)
    assert result is not None
    assert result[0] == [0x6261]
    assert warnings == ["truncated file data"]
