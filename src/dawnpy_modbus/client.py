# tools/dawnpy/src/dawnpy/modbus/client.py
#
# SPDX-License-Identifier: Apache-2.0
#

"""Modbus client helpers and descriptor mapping utilities."""

from __future__ import annotations

import logging
import math
import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    SupportsFloat,
    SupportsInt,
    cast,
)

from dawnpy.descriptor.support.utils import resolve_references
from pymodbus.client import ModbusSerialClient

if TYPE_CHECKING:
    from dawnpy.descriptor.client import ClientDescriptor

logger = logging.getLogger(__name__)

DTYPE_BYTE_SIZES: dict[str, int] = {
    "bool": 1,
    "int8": 1,
    "uint8": 1,
    "int16": 2,
    "uint16": 2,
    "int32": 4,
    "uint32": 4,
    "int64": 8,
    "uint64": 8,
    "float": 4,
    "double": 8,
    "b16": 4,
    "ub16": 4,
}

BIT_REGISTER_TYPES = {"coil", "coil_packed", "discrete", "discrete_packed"}
WORD_REGISTER_TYPES = {"input", "holding"}
SEEKABLE_REGISTER_TYPES = {"seekable"}

Numeric = SupportsFloat | SupportsInt


def _normalize_dtype(dtype: str) -> str:
    return dtype.lower().strip()


def registers_needed(dtype: str) -> int:
    """Return how many 16-bit registers are needed for the dtype."""
    dtype = _normalize_dtype(dtype)
    size_bytes = DTYPE_BYTE_SIZES.get(dtype, 2)
    return max(1, math.ceil(size_bytes / 2))


def _io_dim(io: Any) -> int:
    dim = int(getattr(io, "config", {}).get("dim", 1))
    return max(1, dim)


def _decode_small_signed(value: int, bits: int) -> int:
    mask = (1 << bits) - 1
    value &= mask
    sign_bit = 1 << (bits - 1)
    if value & sign_bit:
        value -= 1 << bits
    return value


def value_from_registers(dtype: str, registers: Sequence[int]) -> object:
    """Decode Modbus register values into a python primitive."""
    if not registers:
        raise ValueError("no registers to decode")

    dtype = _normalize_dtype(dtype)
    if dtype == "bool":
        return bool(registers[0])

    data = _registers_to_bytes(registers)
    decoder = _REGISTER_DECODERS.get(dtype)
    if decoder:
        return decoder(data, registers)
    return list(registers)


def registers_from_value(dtype: str, value: Numeric) -> list[int]:
    """Encode a python value into a Modbus register payload."""
    dtype = _normalize_dtype(dtype)
    encoder = _REGISTER_ENCODERS.get(dtype)
    if not encoder:
        raise ValueError(f"unsupported dtype for register write: {dtype}")

    data = encoder(value)
    return _bytes_to_registers(data)


def _registers_to_bytes(registers: Sequence[int]) -> bytes:
    data = bytearray()
    for reg in registers:
        data.extend(int(reg).to_bytes(2, "big", signed=False))
    return bytes(data)


def _bytes_to_registers(data: bytes) -> list[int]:
    registers: list[int] = []
    for idx in range(0, len(data), 2):
        chunk = data[idx : idx + 2]
        if len(chunk) < 2:
            chunk = chunk + b"\x00"
        registers.append(int.from_bytes(chunk, "big", signed=False))
    return registers


def _decode_uint8(data: bytes, _: Sequence[int]) -> int:
    return data[1]


def _decode_int8(data: bytes, _: Sequence[int]) -> int:
    return _decode_small_signed(data[1], 8)


def _decode_uint16(data: bytes, _: Sequence[int]) -> int:
    return int.from_bytes(data[:2], "big", signed=False)


def _decode_int16(data: bytes, _: Sequence[int]) -> int:
    return int.from_bytes(data[:2], "big", signed=True)


def _decode_uint32(data: bytes, _: Sequence[int]) -> int:
    return int.from_bytes(data[:4], "big", signed=False)


def _decode_int32(data: bytes, _: Sequence[int]) -> int:
    return int.from_bytes(data[:4], "big", signed=True)


def _decode_float(data: bytes, _: Sequence[int]) -> float:
    return cast("float", struct.unpack(">f", data[:4])[0])


def _decode_double(data: bytes, _: Sequence[int]) -> float:
    return cast("float", struct.unpack(">d", data[:8])[0])


def _decode_uint64(data: bytes, _: Sequence[int]) -> int:
    return int.from_bytes(data[:8], "big", signed=False)


def _decode_int64(data: bytes, _: Sequence[int]) -> int:
    return int.from_bytes(data[:8], "big", signed=True)


def _decode_b16(data: bytes, _: Sequence[int]) -> float:
    raw = int.from_bytes(data[:4], "big", signed=True)
    return raw / 65_536


def _decode_ub16(data: bytes, _: Sequence[int]) -> float:
    raw = int.from_bytes(data[:4], "big", signed=False)
    return raw / 65_536


def _encode_uint16(value: Numeric) -> bytes:
    int_value = int(cast("SupportsInt", value))
    scaled = int_value & 0xFFFF
    return struct.pack(">H", scaled)


def _encode_int16(value: Numeric) -> bytes:
    return struct.pack(">h", int(cast("SupportsInt", value)))


def _encode_uint32(value: Numeric) -> bytes:
    return struct.pack(">I", int(cast("SupportsInt", value)))


def _encode_int32(value: Numeric) -> bytes:
    return struct.pack(">i", int(cast("SupportsInt", value)))


def _encode_float(value: Numeric) -> bytes:
    return struct.pack(">f", float(cast("SupportsFloat", value)))


def _encode_double(value: Numeric) -> bytes:
    return struct.pack(">d", float(cast("SupportsFloat", value)))


def _encode_uint64(value: Numeric) -> bytes:
    return struct.pack(">Q", int(cast("SupportsInt", value)))


def _encode_int64(value: Numeric) -> bytes:
    return struct.pack(">q", int(cast("SupportsInt", value)))


def _encode_b16(value: Numeric) -> bytes:
    scaled = int(round(float(cast("SupportsFloat", value)) * 65_536))
    return struct.pack(">i", scaled)


def _encode_ub16(value: Numeric) -> bytes:
    scaled = int(round(float(cast("SupportsFloat", value)) * 65_536))
    return struct.pack(">I", scaled)


_REGISTER_DECODERS: dict[str, Callable[[bytes, Sequence[int]], object]] = {
    "uint8": _decode_uint8,
    "int8": _decode_int8,
    "uint16": _decode_uint16,
    "int16": _decode_int16,
    "uint32": _decode_uint32,
    "int32": _decode_int32,
    "float": _decode_float,
    "double": _decode_double,
    "uint64": _decode_uint64,
    "int64": _decode_int64,
    "b16": _decode_b16,
    "ub16": _decode_ub16,
}


_REGISTER_ENCODERS: dict[str, Callable[[Numeric], bytes]] = {
    "uint8": _encode_uint16,
    "uint16": _encode_uint16,
    "int8": _encode_int16,
    "int16": _encode_int16,
    "uint32": _encode_uint32,
    "int32": _encode_int32,
    "float": _encode_float,
    "double": _encode_double,
    "uint64": _encode_uint64,
    "int64": _encode_int64,
    "b16": _encode_b16,
    "ub16": _encode_ub16,
}


@dataclass
class ModbusBinding:
    """Represents how an IO maps to a Modbus address."""

    io_id: str
    dtype: str
    rw: bool
    group_index: int
    group_type: str
    start_address: int
    register_count: int


@dataclass
class ModbusRegisterGroup:
    """Describes a register block defined in the descriptor."""

    index: int
    reg_type: str
    start: int
    total_registers: int
    bindings: list[ModbusBinding]


class ModbusDescriptorInfo:
    """Parser that exposes Modbus register mapping for a descriptor."""

    def __init__(self, descriptor: ClientDescriptor, unit: int) -> None:
        """Initialize register mapping for the descriptor."""
        self.descriptor = descriptor
        self.unit = unit
        self.protocol = descriptor.get_protocol("modbus_rtu")
        if not self.protocol:
            raise ValueError("descriptor has no modbus_rtu protocol entry")

        self.config = self.protocol.config or {}
        self.path = self.config.get("path")
        self.register_groups = self._build_register_groups()
        self.binding_map: dict[str, ModbusBinding] = {
            binding.io_id: binding
            for group in self.register_groups
            for binding in group.bindings
        }

    def _build_register_groups(self) -> list[ModbusRegisterGroup]:
        registers = self.config.get("registers")
        if not registers:
            raise ValueError(
                "modbus_rtu protocol must define config.registers"
            )

        groups: list[ModbusRegisterGroup] = []
        for idx, reg in enumerate(registers):
            reg_type = str(reg.get("type", "")).lower()
            if reg_type not in BIT_REGISTER_TYPES.union(
                WORD_REGISTER_TYPES
            ).union(SEEKABLE_REGISTER_TYPES):
                raise ValueError(f"unsupported register type '{reg_type}'")

            start = int(reg.get("start", 0))
            reg_config = int(reg.get("config", 0))
            binding_ids = resolve_references(reg.get("bindings", []))
            bindings: list[ModbusBinding] = []
            offset = 0
            for io_id in binding_ids:
                io = self.descriptor.get_io(io_id)
                if io is None:
                    raise ValueError(
                        f"unknown IO '{io_id}' in modbus registers"
                    )
                dtype = io.dtype or "uint16"
                if reg_type in BIT_REGISTER_TYPES:
                    count = _io_dim(io)
                elif reg_type in SEEKABLE_REGISTER_TYPES:
                    seekable_window = reg_config if reg_config > 0 else 8
                    count = seekable_window + 1
                else:
                    count = registers_needed(dtype) * _io_dim(io)
                binding = ModbusBinding(
                    io_id=io_id,
                    dtype=dtype,
                    rw=io.rw,
                    group_index=idx,
                    group_type=reg_type,
                    start_address=start + offset,
                    register_count=count,
                )
                bindings.append(binding)
                offset += count
            groups.append(
                ModbusRegisterGroup(
                    index=idx,
                    reg_type=reg_type,
                    start=start,
                    total_registers=offset,
                    bindings=bindings,
                )
            )
        return groups

    def get_binding(self, io_id: str) -> ModbusBinding | None:
        """Return binding info for the requested IO id."""
        return self.binding_map.get(io_id)


class ModbusClient:
    """Simple wrapper around PyModbus serial client."""

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        parity: str = "E",
        stopbits: int = 1,
        timeout: float = 1.0,
        bytesize: int = 8,
        handle_local_echo: bool = False,
    ) -> None:
        """Store serial parameters before opening the port."""
        self.port = port
        self.baudrate = baudrate
        self.parity = parity.upper()
        self.stopbits = stopbits
        self.timeout = timeout
        self.bytesize = bytesize
        self.handle_local_echo = handle_local_echo
        self._client: ModbusSerialClient | None = None

    def connect(self) -> bool:
        """Open the Modbus serial port."""
        self._client = ModbusSerialClient(
            port=self.port,
            baudrate=self.baudrate,
            parity=self.parity,
            stopbits=self.stopbits,
            timeout=self.timeout,
            bytesize=self.bytesize,
            handle_local_echo=self.handle_local_echo,
        )
        connected = self._client.connect()
        if not connected:
            logger.error("failed to open Modbus port %s", self.port)
        else:
            logger.debug(
                "opened modbus port=%s baudrate=%s parity=%s stopbits=%s "
                "timeout=%s",
                self.port,
                self.baudrate,
                self.parity,
                self.stopbits,
                self.timeout,
            )
        return connected

    def close(self) -> None:
        """Shut down the Modbus client."""
        if self._client:
            client: Any = self._client
            client.close()
            self._client = None

    def _call(self, method: str, *args: Any, **kwargs: Any) -> object | None:
        if not self._client:
            raise RuntimeError("modbus client is not connected")
        fn = getattr(self._client, method, None)
        if not fn:
            raise AttributeError(f"{method} is not supported by Modbus client")
        logger.debug(
            "modbus request %s args=%s kwargs=%s", method, args, kwargs
        )
        try:
            result = cast("object", fn(*args, **kwargs))
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("modbus %s call failed: %s", method, exc)
            return None
        if result is None:
            logger.debug("modbus response %s is None", method)
            return None
        if getattr(result, "isError", lambda: False)():
            logger.debug(
                "modbus error response %s type=%s exc=%s message=%s",
                method,
                type(result).__name__,
                getattr(result, "exception_code", None),
                getattr(result, "message", None),
            )
            return None
        logger.debug("modbus response %s value=%r", method, result)
        return result

    def read_coils(self, unit: int, address: int, count: int) -> object | None:
        """Read coil registers."""
        return self._call("read_coils", address, count=count, device_id=unit)

    def read_discrete_inputs(
        self, unit: int, address: int, count: int
    ) -> object | None:
        """Read discrete input registers."""
        return self._call(
            "read_discrete_inputs",
            address,
            count=count,
            device_id=unit,
        )

    def read_holding_registers(
        self, unit: int, address: int, count: int
    ) -> object | None:
        """Read holding registers."""
        return self._call(
            "read_holding_registers", address, count=count, device_id=unit
        )

    def read_input_registers(
        self, unit: int, address: int, count: int
    ) -> object | None:
        """Read input registers."""
        return self._call(
            "read_input_registers", address, count=count, device_id=unit
        )

    def write_coil(self, unit: int, address: int, value: bool) -> bool:
        """Write a single coil."""
        result = self._call("write_coil", address, value, device_id=unit)
        return result is not None

    def write_registers(
        self, unit: int, address: int, values: Sequence[int]
    ) -> bool:
        """Write multiple registers."""
        result = self._call(
            "write_registers", address, list(values), device_id=unit
        )
        return result is not None
