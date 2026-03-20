#!/usr/bin/env python3
# tools/dawnpy/src/dawnpy/modbus/console.py
#
# SPDX-License-Identifier: Apache-2.0
#

"""Interactive Modbus RTU console client."""

import logging
from collections.abc import Callable
from typing import Any, SupportsFloat, cast

from dawnpy.cli.console_base import ConsoleBase
from dawnpy.cli.device_registry import DeviceConflictError
from dawnpy.cli.table import print_table
from dawnpy.descriptor.client import (
    find_descriptor_path,
    load_client_descriptor,
)
from dawnpy.descriptor.definitions.summary import (
    ObjectIdResolver,
    build_io_table,
)
from dawnpy.descriptor.encoding.proto_caps import validate_descriptor_args
from dawnpy.descriptor.validation.conflicts import check_key_conflicts

from dawnpy_modbus.client import (
    BIT_REGISTER_TYPES,
    ModbusBinding,
    ModbusClient,
    ModbusDescriptorInfo,
    registers_from_value,
    value_from_registers,
)

logger = logging.getLogger(__name__)


class ModbusConsole(ConsoleBase):  # pragma: no cover
    """Interactive Modbus console."""

    def __init__(
        self,
        descriptor_paths: list[str],
        *,
        port: str | None = None,
        baudrate: int = 115200,
        parity: str = "E",
        stopbits: int = 1,
        timeout: float = 1.0,
        unit: int = 1,
        kconfig_path: str | None = None,
        kconfig_overrides: list[dict[str, object]] | None = None,
    ) -> None:
        """Initialize Modbus console."""
        validate_descriptor_args("modbus_rtu", descriptor_paths)
        self.descriptor_paths = [
            find_descriptor_path(path) for path in descriptor_paths
        ]
        self.port = port
        self.baudrate = baudrate
        self.parity = parity.upper()
        self.stopbits = stopbits
        self.timeout = timeout
        self.unit = unit
        self.descriptors = []
        for idx, path in enumerate(self.descriptor_paths):
            overrides = None
            if kconfig_overrides:
                overrides = kconfig_overrides[idx]
            self.descriptors.append(
                load_client_descriptor(
                    path,
                    kconfig_path=kconfig_path,
                    kconfig_overrides=overrides,
                )
            )
        conflicts = check_key_conflicts(
            [(path, []) for path in self.descriptor_paths]
        )
        if conflicts:
            raise DeviceConflictError(conflicts)
        super().__init__(
            prompt="\nEnter command (h for help): ",
            history_file=".dawnpy_modbus_history",
        )
        self.objid_resolver = ObjectIdResolver()
        self.descriptor_infos: list[ModbusDescriptorInfo] = []
        self.clients: list[ModbusClient] = []
        self._setup_clients()
        self._objid_maps = self._build_objid_maps()

    def _setup_clients(self) -> None:
        """Build descriptor-mapped helpers and clients."""
        for idx, desc in enumerate(self.descriptors):
            info = ModbusDescriptorInfo(desc, unit=self.unit)
            port = self.port or info.path
            if not port:
                raise ValueError(
                    f"descriptor {self.descriptor_paths[idx]} "
                    "requires a serial port (--port)"
                )
            client = ModbusClient(
                port=port,
                baudrate=self.baudrate,
                parity=self.parity,
                stopbits=self.stopbits,
                timeout=self.timeout,
            )
            self.descriptor_infos.append(info)
            self.clients.append(client)

    def _build_objid_maps(self) -> list[dict[int, str]]:
        """Build per-descriptor Object ID -> IO ID mappings."""
        maps: list[dict[int, str]] = []
        for desc in self.descriptors:
            mapping: dict[int, str] = {}
            for io in desc.ios.values():
                objid = self.objid_resolver.io_objid(io)
                if objid is not None:
                    mapping[objid] = io.io_id
            maps.append(mapping)
        return maps

    def show_menu(self) -> None:
        """Show console menu."""
        self.print_menu(
            "Modbus Console - Commands",
            [
                "devices: List loaded devices",
                "l: List IOs and register bindings",
                "i [node] <objid>: Show IO details",
                "r [node] <objid>: Read value",
                "w [node] <objid> <value>: Write value",
                "h: Show this help message",
                "q: Quit",
            ],
        )

    def start(self) -> None:
        """Connect clients."""
        print(
            f"\nModbus Console - {len(self.descriptors)} "
            "descriptor(s) configured"
        )
        for idx, client in enumerate(self.clients):
            connected = client.connect()
            if connected:
                print(
                    f"Node {idx}: connected to {client.port} "
                    f"(unit {self.unit})"
                )
            else:
                self.warn(
                    f"Node {idx}: failed to open {client.port}; commands "
                    "will fail until connection succeeds"
                )

    def stop(self) -> None:
        """Close all clients."""
        for client in self.clients:
            client.close()

    def commands_no_args(self) -> dict[str, Callable[[], None]]:
        """Return Modbus commands that do not take arguments."""
        return {
            "devices": self.cmd_devices,
            "l": self.cmd_list_ios,
        }

    def commands_with_args(self) -> dict[str, Callable[[str], None]]:
        """Return Modbus commands that take arguments."""
        return {
            "i": self.cmd_show_info,
            "r": self.cmd_read,
            "read": self.cmd_read,
            "w": self.cmd_write,
            "write": self.cmd_write,
        }

    def on_exit_command(self) -> None:
        """Render the Modbus exit message."""
        self.info("Exiting Modbus console.")

    def cmd_devices(self) -> None:
        """List descriptors."""
        self._handle_devices("")

    def cmd_list_ios(self) -> None:
        """List IOs for every descriptor."""
        self._handle_list_ios("")

    def cmd_show_info(self, args: str) -> None:
        """Show IO information."""
        self._handle_show_info(f"i {args}".strip())

    def cmd_read(self, args: str) -> None:
        """Read IO from Modbus device."""
        self._handle_read(f"r {args}".strip())

    def cmd_write(self, args: str) -> None:
        """Write IO value to device."""
        self._handle_write(f"w {args}".strip())

    def _handle_devices(self, _: str) -> None:
        """List descriptors."""
        print("\nDevices:")
        for idx, path in enumerate(self.descriptor_paths):
            port = self.clients[idx].port
            print(f"  Node {idx}: {path} (port={port}, unit={self.unit})")

    def _handle_list_ios(self, _: str) -> None:
        """List IOs for every descriptor."""
        for idx, desc in enumerate(self.descriptors):
            print()
            print(f"Node {idx}: {self.descriptor_paths[idx]}")
            headers, rows = build_io_table(
                desc,
                resolver=self.objid_resolver,
                methods_lookup=self._binding_description(idx),
            )
            rows.sort(key=self._objid_sort_key)
            print_table(headers, rows)

    @staticmethod
    def _objid_sort_key(row: list[str]) -> tuple[int, int]:
        """Sort helper for rows whose first column is Object ID."""
        try:
            return (0, int(row[0], 16))
        except (ValueError, IndexError):
            return (1, 0)

    def _handle_show_info(self, cmd_line: str) -> None:
        """Show IO info for descriptor+io."""
        args = self._split_args(cmd_line)
        node_idx, tokens = self._extract_node_idx(args)
        if not self._ensure_node(node_idx, "info"):
            return
        if not tokens:
            self.warn("Missing Object ID.")
            return
        resolved = self._resolve_objid(node_idx, tokens[0])
        if not resolved:
            return
        objid, io_id = resolved
        binding = self._binding_for(node_idx, io_id)
        if not binding:
            self.warn(f"No binding for IO {io_id}")
            return
        print(f"\nNode {node_idx} IO {io_id}")
        print(f"  Object ID: 0x{objid:08X}")
        print(f"  Type: {binding.group_type}")
        print(f"  Address: {binding.start_address}")
        print(f"  Registers: {binding.register_count}")
        print(f"  Read/Write: {'rw' if binding.rw else 'r'}")

    def _handle_read(self, cmd_line: str) -> None:
        """Read IO from Modbus device."""
        args = self._split_args(cmd_line)
        node_idx, tokens = self._extract_node_idx(args)
        if not self._ensure_node(node_idx, "read"):
            return
        if not tokens:
            self.warn("Specify Object ID to read.")
            return
        resolved = self._resolve_objid(node_idx, tokens[0])
        if not resolved:
            return
        objid, io_id = resolved
        binding = self._binding_for(node_idx, io_id)
        if not binding:
            self.warn(f"No binding for IO {io_id}")
            return
        client = self.clients[node_idx]
        read_result = self._read_binding(node_idx, client, binding)
        if read_result is None:
            self.warn("Modbus read failed.")
            return
        value, extra = read_result
        self.ok(f"Node {node_idx} {io_id} (0x{objid:08X}) = {value}")
        if extra:
            self.info(extra)

    def _handle_write(self, cmd_line: str) -> None:
        """Write IO value to device."""
        args = self._split_args(cmd_line)
        node_idx, tokens = self._extract_node_idx(args)
        if not self._ensure_node(node_idx, "write"):
            return
        if len(tokens) < 2:
            self.warn("Usage: w [node] <objid> <value>")
            return
        resolved = self._resolve_objid(node_idx, tokens[0])
        if not resolved:
            return
        objid, io_id = resolved
        raw_value = tokens[1]
        binding = self._binding_for(node_idx, io_id)
        if not binding:
            self.warn(f"No binding for IO {io_id}")
            return
        if not binding.rw:
            self.warn(f"IO {io_id} is not writable.")
            return
        client = self.clients[node_idx]
        parsed_value = self._parse_value(
            raw_value, binding.group_type, binding.dtype
        )
        if parsed_value is None:
            self.warn("Failed to parse value.")
            return
        success = self._write_binding(client, binding, parsed_value)
        if not success:
            self.warn("Modbus write failed.")
            return
        self.ok(
            f"Node {node_idx} {io_id} (0x{objid:08X}) set to {parsed_value}"
        )

    def _binding_description(self, node_idx: int) -> Callable[[str], str]:
        def _desc(io_id: str) -> str:
            binding = self._binding_for(node_idx, io_id)
            if not binding:
                return "-"
            rw = "rw" if binding.rw else "r"
            return (
                f"{binding.group_type}@{binding.start_address}"
                f"[{binding.register_count}] {rw}"
            )

        return _desc

    def _binding_for(self, node_idx: int, io_id: str) -> ModbusBinding | None:
        if node_idx >= len(self.descriptor_infos):
            return None
        return self.descriptor_infos[node_idx].get_binding(io_id)

    def _read_binding(
        self, node_idx: int, client: ModbusClient, binding: ModbusBinding
    ) -> tuple[object, str | None] | None:
        if binding.group_type in ("coil_packed", "discrete_packed"):
            packed = self._read_packed_bit_binding(node_idx, client, binding)
            if packed is None:
                return None
            value, encoded, wire_addr, bit_count = packed
            extra = (
                f"Packed byte @ wire {wire_addr}: 0x{encoded:02X} "
                f"({bit_count} bits)"
            )
            return value, extra

        addr = self._wire_address(binding.start_address)
        response = self._read_binding_response(client, binding, addr)
        if response is None:
            logger.debug(
                "read binding returned no response type=%s addr=%s",
                binding.group_type,
                addr,
            )
            return None
        registers = self._response_registers(response)
        if not registers:
            logger.debug(
                "response contained empty registers/bits: %r", response
            )
            return None
        try:
            decoded = value_from_registers(binding.dtype, registers)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "failed to decode registers dtype=%s registers=%s error=%s",
                binding.dtype,
                registers,
                exc,
            )
            return None
        logger.debug(
            "decoded binding addr=%s dtype=%s registers=%s value=%r",
            addr,
            binding.dtype,
            registers,
            decoded,
        )
        return decoded, None

    def _read_binding_response(
        self, client: ModbusClient, binding: ModbusBinding, addr: int
    ) -> object | None:
        logger.debug(
            "reading binding type=%s addr=%s wire_addr=%s count=%s dtype=%s "
            "unit=%s",
            binding.group_type,
            binding.start_address,
            addr,
            binding.register_count,
            binding.dtype,
            self.unit,
        )
        if binding.group_type in BIT_REGISTER_TYPES:
            if binding.group_type.startswith("coil"):
                return client.read_coils(
                    self.unit, addr, binding.register_count
                )
            return client.read_discrete_inputs(
                self.unit, addr, binding.register_count
            )
        if binding.group_type == "holding":
            return client.read_holding_registers(
                self.unit, addr, binding.register_count
            )
        return client.read_input_registers(
            self.unit, addr, binding.register_count
        )

    def _response_registers(self, response: object) -> list[int] | None:
        registers_obj = cast("Any", getattr(response, "registers", None))
        if registers_obj:
            return [int(value) for value in registers_obj]
        bits_obj = cast("Any", getattr(response, "bits", None))
        if bits_obj is None or len(bits_obj) == 0:
            return None
        registers = [1 if bits_obj[0] else 0]
        logger.debug("converted bit response to registers=%s", registers)
        return registers

    def _read_packed_bit_binding(
        self, node_idx: int, client: ModbusClient, binding: ModbusBinding
    ) -> tuple[bool, int, int, int] | None:
        """Read packed bit IO and the full packed byte containing it."""
        if node_idx >= len(self.descriptor_infos):
            return None
        groups = self.descriptor_infos[node_idx].register_groups
        group = next(
            (g for g in groups if g.index == binding.group_index), None
        )
        if group is None:
            return None

        bit_index = next(
            (
                idx
                for idx, group_binding in enumerate(group.bindings)
                if group_binding.io_id == binding.io_id
            ),
            -1,
        )
        if bit_index < 0:
            return None

        byte_base = (bit_index // 8) * 8
        bit_in_byte = bit_index - byte_base
        bit_count = min(8, group.total_registers - byte_base)
        if bit_count <= 0:
            return None

        addr = self._wire_address(group.start + byte_base)
        logger.debug(
            "reading packed-bit io=%s type=%s group_start=%s bit_index=%s "
            "byte_base=%s wire_addr=%s count=%s",
            binding.io_id,
            binding.group_type,
            group.start,
            bit_index,
            byte_base,
            addr,
            bit_count,
        )
        if binding.group_type == "discrete_packed":
            response = client.read_discrete_inputs(self.unit, addr, bit_count)
        else:
            response = client.read_coils(self.unit, addr, bit_count)
        if response is None:
            return None

        bits = getattr(response, "bits", None)
        if bits is None or len(bits) <= bit_in_byte:
            logger.debug("packed-bit response missing bits: %r", response)
            return None

        encoded = 0
        for bit_pos in range(min(len(bits), 8)):
            if bool(bits[bit_pos]):
                encoded |= 1 << bit_pos

        return bool(bits[bit_in_byte]), encoded, addr, bit_count

    def _write_binding(
        self,
        client: ModbusClient,
        binding: ModbusBinding,
        value: SupportsFloat,
    ) -> bool:
        if binding.group_type in ("input", "discrete", "discrete_packed"):
            self.warn(
                f"Modbus group '{binding.group_type}' is read-only "
                f"(io={binding.io_id})"
            )
            return False

        addr = self._wire_address(binding.start_address)
        if binding.group_type in BIT_REGISTER_TYPES:
            return client.write_coil(self.unit, addr, bool(value))
        try:
            registers = registers_from_value(binding.dtype, value)
        except ValueError as exc:
            self.warn(str(exc))
            return False
        return client.write_registers(self.unit, addr, registers)

    @staticmethod
    def _wire_address(start_address: int) -> int:
        """Convert descriptor register number to Modbus wire address."""
        return max(0, start_address - 1)

    def _parse_value(
        self, raw: str, group_type: str, dtype: str
    ) -> SupportsFloat | None:
        raw_lower = raw.lower()
        if dtype.lower() == "bool":
            return raw_lower in ("1", "true", "on", "yes")
        if group_type in BIT_REGISTER_TYPES:
            return raw_lower in ("1", "true", "on", "yes")
        try:
            if "." in raw or dtype.lower() in (
                "float",
                "double",
                "b16",
                "ub16",
            ):
                return float(raw)
            return int(raw, 0)
        except ValueError:
            return None

    def _split_args(self, cmd_line: str) -> list[str]:
        return cmd_line.strip().split()[1:]

    def _extract_node_idx(self, tokens: list[str]) -> tuple[int, list[str]]:
        if len(tokens) >= 2 and tokens[0].isdigit():
            node = int(tokens[0])
            return node, tokens[1:]
        return 0, tokens

    def _ensure_node(self, node_idx: int, cmd_name: str) -> bool:
        if 0 <= node_idx < len(self.descriptor_infos):
            return True
        max_idx = max(0, len(self.descriptor_infos) - 1)
        self.warn(
            f"Node index {node_idx} is out of range for {cmd_name} "
            f"(0-{max_idx})"
        )
        return False

    def _parse_objid(self, objid_str: str) -> int | None:
        """Parse object ID strings (hex or decimal)."""
        try:
            return int(objid_str, 0)
        except ValueError:
            self.warn("Invalid Object ID (use hex like 0x00010001)")
            return None

    def _resolve_objid(
        self, node_idx: int, objid_str: str
    ) -> tuple[int, str] | None:
        """Resolve object ID to an IO id for the given node."""
        objid = self._parse_objid(objid_str)
        if objid is None:
            return None
        mapping = self._objid_maps[node_idx]
        io_id = mapping.get(objid)
        if io_id is None:
            self.warn(f"Object ID 0x{objid:08X} not found on node {node_idx}")
            return None
        return objid, io_id


def run_console(
    descriptor_paths: list[str],
    *,
    port: str | None = None,
    baudrate: int = 115200,
    parity: str = "E",
    stopbits: int = 1,
    timeout: float = 1.0,
    unit: int = 1,
    kconfig_path: str | None = None,
    kconfig_overrides: list[dict[str, object]] | None = None,
) -> None:  # pragma: no cover
    """Run Modbus console."""
    console = ModbusConsole(
        descriptor_paths,
        port=port,
        baudrate=baudrate,
        parity=parity,
        stopbits=stopbits,
        timeout=timeout,
        unit=unit,
        kconfig_path=kconfig_path,
        kconfig_overrides=kconfig_overrides,
    )
    console.run()
