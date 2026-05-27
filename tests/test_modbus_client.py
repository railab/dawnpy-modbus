# tools/dawnpy/tests/test_modbus_client.py
#
# SPDX-License-Identifier: Apache-2.0
#

from pathlib import Path
from types import SimpleNamespace

import pytest
from dawnpy.descriptor.client import load_client_descriptor
from dawnpy.descriptor.definitions.registry import IOTypeInfo, ProtoTypeInfo

from dawnpy_modbus.client import (
    ModbusClient,
    ModbusDescriptorInfo,
    _bytes_to_registers,
    _registers_to_bytes,
    registers_from_value,
    registers_needed,
    value_from_registers,
)
from dawnpy_modbus.console import ModbusConsole


@pytest.fixture(autouse=True)
def descriptor_types(monkeypatch):
    """Provide only descriptor types used by this test module."""
    from dawnpy.descriptor.definitions import registry

    registry.reset_type_registry()
    monkeypatch.setattr(registry, "_REGISTRY_LOADED", True)
    registry._IO_TYPES_DATA.update(
        {
            "dummy": IOTypeInfo(
                cpp_class="CIODummy",
                header="dawn/io/dummy.hxx",
                helper_func="{cpp_class}::objectId",
                params=["dtype", "timestamp", "instance"],
            ),
            "descriptor": IOTypeInfo(
                cpp_class="CIODescriptor",
                header="dawn/io/descriptor.hxx",
                helper_func="{cpp_class}::objectId",
                params=["instance"],
            ),
            "capabilities": IOTypeInfo(
                cpp_class="CIOCapabilities",
                header="dawn/io/capabilities.hxx",
                helper_func="{cpp_class}::objectId",
                params=["instance"],
            ),
            "fileio": IOTypeInfo(
                cpp_class="CIOFile",
                header="dawn/io/fileio.hxx",
                helper_func="{cpp_class}::objectId",
                params=["instance"],
            ),
        }
    )
    registry._PROTO_TYPES_DATA["modbus_rtu"] = ProtoTypeInfo(
        cpp_class="CProtoModbusRtu",
        header="dawn/proto/modbus/rtu.hxx",
    )
    yield
    registry.reset_type_registry()


class _FakeResponse:
    def __init__(self, *, registers=None, bits=None, error=False):
        self.registers = registers or [0xABCD]
        self.bits = bits or [True]
        self._error = error

    def isError(self) -> bool:  # noqa: N802
        return self._error


class _FakeSerialClient:
    def __init__(self, result, *, connect_ok: bool = True):
        self._result = result
        self.closed = False
        self.calls: list[str] = []
        self.connect_ok = connect_ok

    def connect(self) -> bool:
        self.calls.append("connect")
        return self.connect_ok

    def close(self) -> None:
        self.closed = True

    def read_coils(self, address, *, count=1, device_id=1):
        self.calls.append("read_coils")
        return self._result

    def read_discrete_inputs(self, address, *, count=1, device_id=1):
        self.calls.append("read_discrete_inputs")
        return self._result

    def read_holding_registers(self, address, *, count=1, device_id=1):
        self.calls.append("read_holding_registers")
        return self._result

    def read_input_registers(self, address, *, count=1, device_id=1):
        self.calls.append("read_input_registers")
        return self._result

    def write_coil(self, address, value, *, device_id=1):
        self.calls.append("write_coil")
        return self._result

    def write_registers(self, address, values, *, device_id=1):
        self.calls.append("write_registers")
        return self._result


class _NoAddrKwClient:
    def read_coils(self, address, *, count=1):
        return _FakeResponse()


class _SimpleDescriptor:
    def __init__(self, proto, ios=None):
        self._proto = proto
        self._ios = ios or {}

    def get_protocol(self, proto_type):
        return self._proto

    def get_io(self, io_id):
        return self._ios.get(io_id)


def _descriptor_path() -> Path:
    return Path(__file__).parent / "fixtures" / "modbus_rtu_dummy_map.yaml"


def test_registers_needed_variants() -> None:
    assert registers_needed("bool") == 1
    assert registers_needed("int16") == 1
    assert registers_needed("uint32") == 2
    assert registers_needed("double") == 4


def test_register_round_trip_float() -> None:
    registers = registers_from_value("float", 123.5)
    assert (
        pytest.approx(value_from_registers("float", registers), rel=1e-6)
        == 123.5
    )


def test_register_round_trip_b16() -> None:
    registers = registers_from_value("b16", -1.5)
    assert (
        pytest.approx(value_from_registers("b16", registers), abs=1e-4) == -1.5
    )


def test_bool_decode() -> None:
    assert value_from_registers("bool", [1])
    assert not value_from_registers("bool", [0])


def test_descriptor_info_builds_bindings() -> None:
    desc = load_client_descriptor(str(_descriptor_path()))
    info = ModbusDescriptorInfo(desc, unit=1)

    assert info.path == "/dev/ttyS1"
    binding = info.get_binding("dummyio1")
    assert binding is not None
    assert binding.start_address == 0
    assert binding.group_type.startswith("coil")
    assert binding.rw is True
    assert len(info.binding_map) == 12

    packed = info.get_binding("dummyio4")
    assert packed is not None
    assert packed.group_type == "coil_packed"
    assert packed.start_address == 1000
    assert packed.register_count == 16
    assert packed.rw is True

    holding = info.get_binding("dummyio20")
    assert holding is not None
    assert holding.rw is True

    input_binding = info.get_binding("dummyio23")
    assert input_binding is not None
    assert input_binding.rw is False

    seekable_desc = info.get_binding("descriptor1")
    assert seekable_desc is not None
    assert seekable_desc.group_type == "seekable"
    assert seekable_desc.start_address == 3600
    assert seekable_desc.register_count == 257
    assert seekable_desc.rw is True

    seekable_caps = info.get_binding("capabilities1")
    assert seekable_caps is not None
    assert seekable_caps.group_type == "seekable"
    assert seekable_caps.start_address == 3900
    assert seekable_caps.register_count == 257


def test_value_from_registers_empty() -> None:
    with pytest.raises(ValueError):
        value_from_registers("int16", [])


def test_value_from_registers_unknown_type() -> None:
    registers = [1, 2]
    assert value_from_registers("custom", registers) == registers


def test_descriptor_info_missing_protocol() -> None:
    desc = _SimpleDescriptor(None)
    with pytest.raises(ValueError, match="descriptor has no modbus_rtu"):
        ModbusDescriptorInfo(desc, unit=1)


def test_descriptor_info_missing_registers() -> None:
    proto = SimpleNamespace(config={"path": "/tmp"})
    desc = _SimpleDescriptor(proto)
    with pytest.raises(ValueError, match="config.registers"):
        ModbusDescriptorInfo(desc, unit=1)


def test_descriptor_info_unsupported_register_type() -> None:
    proto = SimpleNamespace(
        config={
            "path": "/tmp",
            "registers": [{"type": "magic", "bindings": []}],
        }
    )
    desc = _SimpleDescriptor(proto)
    with pytest.raises(ValueError, match="unsupported register type"):
        ModbusDescriptorInfo(desc, unit=1)


def test_descriptor_info_unknown_io() -> None:
    proto = SimpleNamespace(
        config={
            "path": "/tmp",
            "registers": [
                {"type": "holding", "bindings": ["missing"], "start": 5}
            ],
        }
    )
    desc = _SimpleDescriptor(proto)
    with pytest.raises(ValueError, match="unknown IO"):
        ModbusDescriptorInfo(desc, unit=1)


@pytest.mark.parametrize(
    "dtype,value,approx",
    [
        ("uint8", 0x7F, None),
        ("int8", -5, None),
        ("uint16", 0xABCD, None),
        ("int16", -12345, None),
        ("uint32", 0xDEADBEEF, None),
        ("int32", -123456, None),
        ("float", 3.14, 1e-6),
        ("double", -2.71828, 1e-12),
        ("uint64", 0x123456789ABCDEF0, None),
        ("int64", -987654321012345678, None),
        ("b16", 1.5, 1e-4),
        ("ub16", 2.25, 1e-4),
    ],
)
def test_register_round_trip_all_types(
    dtype: str, value: object, approx: float | None
) -> None:
    registers = registers_from_value(dtype, value)
    decoded = value_from_registers(dtype, registers)
    if approx is not None:
        assert pytest.approx(decoded, rel=approx) == value
    else:
        assert decoded == value


def test_register_encoding_unsupported() -> None:
    with pytest.raises(ValueError):
        registers_from_value("natural", 1)


def test_bytes_registers_helpers() -> None:
    registers = [0x1234, 0xABCD, 0x0F]
    data = _registers_to_bytes(registers)
    assert _bytes_to_registers(data) == registers


def test_bytes_to_registers_padding() -> None:
    assert _bytes_to_registers(b"\x01") == [0x0100]


def test_modbus_client_connect_failure(monkeypatch) -> None:
    response = _FakeResponse()

    def factory(*args, **kwargs):
        return _FakeSerialClient(response, connect_ok=False)

    monkeypatch.setattr("dawnpy_modbus.client.ModbusSerialClient", factory)
    client = ModbusClient("/dev/null")
    assert not client.connect()


def test_modbus_client_reads_and_writes(monkeypatch) -> None:
    response = _FakeResponse()

    def factory(*args, **kwargs):
        return _FakeSerialClient(response)

    monkeypatch.setattr("dawnpy_modbus.client.ModbusSerialClient", factory)
    client = ModbusClient("/dev/null")
    assert client.connect()
    assert client.read_coils(1, 1, 1).registers == response.registers
    assert client.read_discrete_inputs(1, 1, 1).registers == response.registers
    assert (
        client.read_holding_registers(1, 1, 1).registers == response.registers
    )
    assert client.read_input_registers(1, 1, 1).registers == response.registers
    assert client.write_coil(1, 1, True)
    assert client.write_registers(1, 1, [0x1234])
    client.close()
    assert client._client is None


def test_modbus_client_call_error_paths(monkeypatch) -> None:
    response = _FakeResponse(error=True)

    def factory(*args, **kwargs):
        return _FakeSerialClient(response)

    monkeypatch.setattr("dawnpy_modbus.client.ModbusSerialClient", factory)
    client = ModbusClient("/dev/null")
    client.connect()
    assert client.read_coils(1, 1, 1) is None
    client._client = _FakeSerialClient(None)  # type: ignore
    assert client.read_coils(1, 1, 1) is None
    with pytest.raises(AttributeError):
        client._call("missing")


def test_modbus_client_call_not_connected() -> None:
    client = ModbusClient("/dev/null")
    with pytest.raises(RuntimeError):
        client._call("read_coils", 1, 1, unit=1)


def test_modbus_client_unsupported_signature_returns_none() -> None:
    client = ModbusClient("/dev/null")
    client._client = _NoAddrKwClient()  # type: ignore
    assert client.read_coils(1, 10, 2) is None
    assert _NoAddrKwClient().read_coils(10, count=1).isError() is False


class _ChunkClient:
    def __init__(self):
        self.calls: list[tuple[str, int, int, int]] = []

    def read_holding_registers(self, unit, address, count):
        self.calls.append(("holding", unit, address, count))
        if count == 1 and address == 3856:
            return _FakeResponse(registers=[512], bits=[])
        return _FakeResponse(
            registers=list(range(address, address + count)),
            bits=[],
        )


class _DynamicInputClient:
    def __init__(self):
        self.calls: list[tuple[str, int, int, int]] = []

    def _resp(self, unit, address, count):
        self.calls.append(("input", unit, address, count))
        if count < 12:
            return SimpleNamespace(
                registers=[],
                bits=[],
                exception_code=4,
                message=None,
                isError=lambda: True,
            )
        if count > 12:
            return SimpleNamespace(
                registers=[],
                bits=[],
                exception_code=2,
                message=None,
                isError=lambda: True,
            )
        return _FakeResponse(registers=list(range(12)), bits=[])

    def read_input_registers(self, unit, address, count):
        response = self._resp(unit, address, count)
        if response.isError():
            return None
        return response

    def read_input_registers_raw(self, unit, address, count):
        return self._resp(unit, address, count)


def test_console_wire_address_preserves_descriptor_address() -> None:
    console = ModbusConsole.__new__(ModbusConsole)
    assert console._wire_address(2000) == 2000
    assert console._wire_address(3600) == 3600


def test_console_read_seekable_uses_holding_and_chunks() -> None:
    console = ModbusConsole.__new__(ModbusConsole)
    console.unit = 1
    console.descriptor_infos = []
    binding = SimpleNamespace(
        io_id="descriptor1",
        dtype="uint32",
        rw=True,
        group_index=0,
        group_type="seekable",
        start_address=3600,
        register_count=257,
    )
    client = _ChunkClient()

    result = console._read_binding(0, client, binding)

    assert result is not None
    value, extra = result
    assert isinstance(value, list)
    assert len(value) == 256
    assert value[:3] == [3600, 3601, 3602]
    assert extra == "Seekable window @ wire 3600: 256 regs, offset=512"
    assert client.calls == [
        ("holding", 1, 3600, 125),
        ("holding", 1, 3725, 125),
        ("holding", 1, 3850, 6),
        ("holding", 1, 3856, 1),
    ]


def test_console_dynamic_input_binding_probes_full_width_and_decodes_vector():
    console = ModbusConsole.__new__(ModbusConsole)
    console.unit = 1
    console.descriptor_infos = [
        SimpleNamespace(
            register_groups=[
                SimpleNamespace(
                    index=0, bindings=[SimpleNamespace(io_id="adc0")]
                )
            ]
        )
    ]
    binding = SimpleNamespace(
        io_id="adc0",
        dtype="uint32",
        rw=False,
        group_index=0,
        group_type="input",
        start_address=512,
        register_count=2,
    )
    client = _DynamicInputClient()

    result = console._read_binding(0, client, binding)

    assert result is not None
    value, extra = result
    assert value == [1, 131075, 262149, 393223, 524297, 655371]
    assert extra is None
    assert binding.register_count == 12
    assert client.calls == [
        ("input", 1, 512, 2),
        ("input", 1, 512, 2),
        ("input", 1, 512, 3),
        ("input", 1, 512, 4),
        ("input", 1, 512, 5),
        ("input", 1, 512, 6),
        ("input", 1, 512, 7),
        ("input", 1, 512, 8),
        ("input", 1, 512, 9),
        ("input", 1, 512, 10),
        ("input", 1, 512, 11),
        ("input", 1, 512, 12),
    ]
