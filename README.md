# M20 BIOS 2.0x

An extended boot ROM for the Olivetti M20: BIOS 2.0f plus a built-in
monitor and a serial loader, in a 16 KB ROM (two 8 KB EPROMs, even/odd).

- Boots exactly as 2.0f when no key is pressed.
- **Hold X at power-on** for the monitor. It can:
  - dump memory and read/write I/O ports;
  - boot from a chosen drive;
  - load programs from a PC over the serial port, verified in memory
    before they run.
- The **disk tool**, loaded from the PC, copies whole disks between the M20
  and disk image files on the PC.

## Contents

| | |
|---|---|
| `rom/` | `m20-20x.bin` (16 KB) and the two EPROM images `m20-20x.even.bin`, `m20-20x.odd.bin` |
| `environments/` | programs for the monitor's loader: `disktool.m20x`, `hello.m20x` (example) |
| `server/` | the PC server (Python 3; pyserial for a real serial port) and its tests |
| `MANUAL.md` | the user manual |
| `SHA256SUMS` | checksums of the binaries |
| `LICENSE` | GNU General Public License, version 3 |

## Quick start

```sh
mkdir m20files && cp environments/*.m20x m20files/
python3 server/m20server.py serve --dir m20files /dev/tty.usbserial-XXXX
```

On the M20: hold **X** while switching on, then type `L` and choose a
program. See [MANUAL.md](MANUAL.md).

## AI disclosure

This software is developed with assistance from AI coding agents and with
humans leading the ideas, testing, and debugging.

## License

The Python server code in `server/` (`m20server.py`, `m20env.py`,
`m20link.py`, `test_m20server.py`) is Copyright (C) 2026 Salvatore Paxia.

It is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. It is distributed WITHOUT ANY WARRANTY; see [LICENSE](LICENSE) for
the full text.

The ROM images contain the original Olivetti BIOS 2.0f in their lower 8 KB.
