# dawnpy-modbus

Modbus transport extension for `dawnpy`.

Main Dawn project: [railab/dawn](https://github.com/railab/dawn).

The package exposes the standalone `dawnpy-modbus` command. It does not add
commands to `dawnpy`.

QA follows the shared Python tool baseline:

```sh
tox
tox -e py
tox -e format
tox -e flake8
tox -e type
```

Seekable IO uses NxModbus's standard Modbus FC20/FC21 file records.
Existing `type: seekable` descriptors, addresses, window widths, and console
`r`/`w` commands are retained. `r` advances a local cursor and returns the
existing register list; `w` writes at that cursor. PyModbus >=3.13,<4 is
required. Update firmware and clients together.

`dawnpy_modbus.files.SeekableFile` provides `read(offset, size)` and
`write(offset, data)` with PDU chunking and file-boundary handling. Writes
use complete two-byte records and retain the backing IO's offset semantics.
See the Dawn Modbus RTU/TCP documentation for the file-number mapping.
