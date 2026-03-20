# tools/dawnpy/src/dawnpy/commands/cmd_modbus.py
#
# SPDX-License-Identifier: Apache-2.0
#

"""Module containing modbus command (skeleton)."""

import click
from dawnpy.cli.device_registry import DeviceConflictError
from dawnpy.cli.environment import Environment, pass_environment
from dawnpy.cli.options import build_kconfig_overrides, configure_cli_logging

from dawnpy_modbus.console import run_console

###############################################################################
# Command: cmd_modbus
###############################################################################


@click.command(name="modbus")
@click.argument(
    "serial_path",
    type=click.Path(resolve_path=False),
    required=True,
)
@click.argument(
    "descriptors",
    type=click.Path(resolve_path=False),
    required=True,
    nargs=-1,
)
@click.option(
    "--baudrate",
    "baudrate",
    type=int,
    default=115200,
    show_default=True,
    help="Serial baudrate for Modbus RTU",
)
@click.option(
    "--parity",
    "parity",
    type=click.Choice(["N", "O", "E"], case_sensitive=False),
    default="E",
    show_default=True,
    help="Serial parity (N/O/E)",
)
@click.option(
    "--stopbits",
    "stopbits",
    type=click.IntRange(1, 2),
    default=1,
    show_default=True,
    help="Serial stop bits",
)
@click.option(
    "--timeout",
    "timeout",
    type=float,
    default=1.0,
    show_default=True,
    help="Serial timeout (seconds)",
)
@click.option(
    "--unit",
    "unit",
    type=int,
    default=1,
    show_default=True,
    help="Modbus slave address",
)
@click.option(
    "--kconfig-var",
    "kconfig_var",
    help="Kconfig symbol name to override (e.g., CONFIG_SIM_MODBUS_BASE)",
)
@click.option(
    "--kconfig-values",
    "kconfig_values",
    help="Comma-separated values for the Kconfig override",
)
@click.option(
    "--debug/--no-debug",
    default=False,
    is_flag=True,
    envvar="DAWNPY_DEBUG",
)
@pass_environment
def cmd_modbus(
    ctx: Environment,
    serial_path: str,
    descriptors: tuple[str, ...],
    baudrate: int,
    parity: str,
    stopbits: int,
    timeout: float,
    unit: int,
    kconfig_var: str | None,
    kconfig_values: str | None,
    debug: bool,
) -> bool:
    """Run Modbus RTU console for descriptor-driven IO access."""
    ctx.debug = debug
    configure_cli_logging(debug)

    try:
        descriptor_list = list(descriptors)
        kconfig_overrides = build_kconfig_overrides(
            descriptor_list,
            kconfig_var,
            kconfig_values,
        )
        if kconfig_overrides and len(descriptor_list) == 1:
            if len(kconfig_overrides) > 1:
                descriptor_list = descriptor_list * len(kconfig_overrides)
        run_console(
            descriptor_list,
            port=serial_path,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            timeout=timeout,
            unit=unit,
            kconfig_overrides=kconfig_overrides,
        )
    except DeviceConflictError as exc:
        raise click.ClickException(exc.format_message()) from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    return True
