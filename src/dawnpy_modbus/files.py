# tools/dawnpy-modbus/src/dawnpy_modbus/files.py
#
# SPDX-License-Identifier: Apache-2.0
#

"""Seekable IO offsets mapped to NxModbus FC20/FC21 file records."""

from __future__ import annotations

from typing import Protocol

MAX_FILE_NUMBER = 65535
FILE_BYTES = 20000
MAX_READ_BYTES = 242
# Leave headroom: current NxModbus RTU drops a completely full RX buffer.
MAX_WRITE_BYTES = 240


class FileClient(Protocol):
    """Transport-independent file record operations."""

    def read_file_records(
        self, unit: int, number: int, record: int, count: int
    ) -> bytes:
        """Read a record group."""
        ...

    def write_file_records(
        self, unit: int, number: int, record: int, data: bytes
    ) -> None:
        """Write a record group, raising OSError on failure."""
        ...


class SeekableFile:
    """Translate byte offsets within one descriptor binding to file records."""

    def __init__(
        self, client: FileClient, unit: int, number: int, count: int
    ) -> None:
        """Bind the first file number and its reserved file count."""
        if count < 1 or number < 1 or number + count - 1 > MAX_FILE_NUMBER:
            raise ValueError("file mapping exceeds Modbus file number range")
        self.client = client
        self.unit = unit
        self.number = number
        self.capacity = count * FILE_BYTES

    def _check_range(self, offset: int, size: int) -> None:
        if offset < 0 or size < 0 or offset + size > self.capacity:
            raise ValueError("offset and size exceed seekable file allocation")

    def read(self, offset: int, size: int) -> bytes:
        """Read bytes at an explicit offset, including zero padding at EOF."""
        self._check_range(offset, size)
        end = offset + size
        data = bytearray()
        while offset < end:
            aligned = offset & ~1
            skip = offset - aligned
            byte_count = min(
                MAX_READ_BYTES,
                FILE_BYTES - aligned % FILE_BYTES,
                end - aligned,
            )
            record_count = (byte_count + 1) // 2
            chunk = self.client.read_file_records(
                self.unit,
                self.number + aligned // FILE_BYTES,
                (aligned % FILE_BYTES) // 2,
                record_count,
            )
            if len(chunk) != record_count * 2:
                raise OSError("truncated file data")
            data.extend(chunk[skip:byte_count])
            offset = aligned + byte_count
        return bytes(data)

    def write(self, offset: int, data: bytes) -> None:
        """Write complete records using the backing IO's offset semantics.

        FC21 carries two-byte records, so both offset and size must be even.
        """
        self._check_range(offset, len(data))
        if offset % 2 or len(data) % 2:
            raise ValueError("file writes require two-byte record alignment")
        consumed = 0
        while consumed < len(data):
            size = min(MAX_WRITE_BYTES, FILE_BYTES - offset % FILE_BYTES)
            chunk = data[consumed : consumed + size]
            self.client.write_file_records(
                self.unit,
                self.number + offset // FILE_BYTES,
                (offset % FILE_BYTES) // 2,
                chunk,
            )
            offset += len(chunk)
            consumed += len(chunk)
