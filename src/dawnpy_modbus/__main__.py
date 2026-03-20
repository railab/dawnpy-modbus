"""Standalone CLI entry point for dawnpy-modbus."""

from dawnpy_modbus.commands.cmd_modbus import cmd_modbus


def main() -> None:
    """Run the Modbus CLI."""
    cmd_modbus(prog_name="dawnpy-modbus")


if __name__ == "__main__":
    main()
