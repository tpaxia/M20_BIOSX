# M20 BIOS 2.0x — User Manual

BIOS 2.0x is the Olivetti M20 boot ROM 2.0f with a built-in monitor and a
serial loader.

- **Stock behaviour kept.** Without a keypress the machine boots exactly as
  with 2.0f.
- **Monitor.** Holding **X** at power-on opens a monitor for inspecting
  memory and I/O ports, booting from a chosen drive, and loading programs
  from a PC over the serial port.
- **Disk images.** A program loaded this way, the *disk tool*, copies whole
  disks between the M20 and disk image files on the PC.

---

## Contents

1. [What is in the package](#1-what-is-in-the-package)
2. [Installing the ROM](#2-installing-the-rom)
3. [Normal start-up](#3-normal-start-up)
4. [Entering the monitor](#4-entering-the-monitor)
5. [Monitor commands](#5-monitor-commands)
6. [Connecting a PC](#6-connecting-a-pc)
7. [The server](#7-the-server)
8. [Loading programs (L)](#8-loading-programs-l)
9. [The disk tool](#9-the-disk-tool)
10. [Writing your own programs](#10-writing-your-own-programs)
11. [Messages](#11-messages)
12. [Limitations](#12-limitations)
13. [Technical reference](#13-technical-reference)
14. [License](#14-license)

---

## 1. What is in the package

| Path | |
|---|---|
| `rom/m20-20x.bin` | the complete 16 KB ROM image |
| `rom/m20-20x.even.bin` | 8 KB EPROM image: even bytes (0, 2, 4, …) |
| `rom/m20-20x.odd.bin` | 8 KB EPROM image: odd bytes (1, 3, 5, …) |
| `environments/disktool.m20x` | the disk tool (section 9) |
| `environments/hello.m20x` | a small example program (section 8) |
| `server/m20server.py` | the PC server, and the tool to build programs |
| `server/m20env.py`, `server/m20link.py` | server modules (program format, serial transport) |
| `server/test_m20server.py` | self-tests of the server; no M20 needed |
| `SHA256SUMS` | checksums of the binaries |

---

## 2. Installing the ROM

The original ROM holds 8 KB, split over an even/odd pair. BIOS 2.0x is
16 KB, split the same way into two 8 KB EPROMs (2764 or equivalent):

1. Check the files: `shasum -a 256 -c SHA256SUMS`.
2. Program `m20-20x.even.bin` into one 2764 and `m20-20x.odd.bin` into the
   other.
3. Fit them in place of the original boot ROM pair: the even image where
   the original even ROM was, the odd image where the odd ROM was. Keep the
   original chips.
4. Switch on. The machine should behave as before, apart from the start-up
   message (section 3).

If the chips are swapped, or one is badly programmed, the BIOS's own ROM
checksum fails during the power-on test. The 2.0x checksum covers all
16 KB. The machine then stops with the stock ROM error display and never
runs a half-installed ROM. Swap or re-program the chips.

---

## 3. Normal start-up

Nothing changes. The power-on test, the "Select Alternate CPU" question,
the drive checks and the boot all work as in 2.0f. The only visible
difference is the boot message:

```
Bootstrap Loader  Rev. 2.0x
```

The stock power-on keys (D, F, L) keep their original meaning. The ROM
routines PCOS and other programs use are at their original addresses.

---

## 4. Entering the monitor

Two ways:

- **Hold X from power-on or reset** until the monitor appears. It opens once
  the power-on test has finished: memory counted, keyboard and drives set
  up. If the "Select Alternate CPU (y/n)?" question appears while X is
  held, the BIOS takes the held key as "no" and continues.
- **Press X at "Insert system disk and type any key."** This works when the
  machine finds no bootable disk.

The screen switches to its full height and clears:

```
M20 BIOS 2.0x  Monitor 1.0  (C) 2026 Salvatore Paxia
Entered from POST (X key)
>
```

The second line reads `Entered from the no-system-disk prompt` when the
monitor was entered from the disk prompt.

The BIOS itself shows only the top half of the screen while it loads the
operating system, because it uses the bottom half of the screen memory as a
work area. The monitor uses the whole screen. It restores the half-height
screen when you leave it with **B**.

---

## 5. Monitor commands

Commands are one letter, upper or lower case, followed by their arguments.

- Numbers are **hexadecimal**.
- Addresses are written `segment:offset`, e.g. `6:4000` (segment 6, offset
  4000). `4:` alone means offset 0 of segment 4. A bare offset stays in the
  current segment.
- Line editing: **BS/DEL** erase a character, **Ctrl-U** erases the line,
  **ESC** or **Ctrl-C** cancel it, **RETURN** executes it.

| Command | Action |
|---|---|
| `D [addr [end \| Llen]]` | Dump memory: 8 bytes per line in hex and ASCII. `D` alone continues where the last dump stopped. The default length is 80 (hex) bytes. A long dump pauses at `-- more --`; ESC, Ctrl-C or Q stop it. |
| `I port` | Read a byte from an I/O port: `I 21` → `0021: F0` |
| `IW port` | Read a word from an I/O port |
| `O port value` | Write a byte to an I/O port |
| `OW port value` | Write a word to an I/O port |
| `B` | Boot, exactly as the BIOS would have done without the monitor. |
| `B 0` / `B 1` / `B 10` | Boot from floppy A / floppy B / the hard disk |
| `L` | Choose a program from the server's list, load and run it (section 8) |
| `L name` | Load and run the program `name` from the server |
| `H` or `?` | Help |

Examples:

```
>d 4:0 l20                 dump the first 32 bytes of the ROM
>i 21                      read the configuration port
>b 1                       boot from floppy B
```

An unknown command or a wrong argument is answered with `? ` and the line.

---

## 6. Connecting a PC

The loader and the disk tool use the M20's serial (RS-232) port at
**9600 baud, 8 data bits, no parity, 1 stop bit**:

- Connect the PC with a **null-modem** cable, for example through a USB
  serial adapter.
- The M20's serial chip (8251) transmits only while its **CTS** input is
  active, so the cable must drive CTS: connect it to the PC's RTS, or loop
  it on the M20 side.
- No software flow control is used.

The M20 keyboard and screen stay the console. The serial port carries only
the transfers.

---

## 7. The server

The server runs on the PC. It needs Python 3 and, for a real serial port,
pyserial (`pip install pyserial`).

```sh
python3 server/m20server.py serve --dir DIRECTORY /dev/tty.usbserial-XXXX
```

It serves the files in `DIRECTORY`:

| File | Used for |
|---|---|
| `NAME.m20x` | programs the monitor can load (`L NAME`, or the `L` menu) |
| `NAME.img` | disk images the disk tool writes to a drive (`W`) |
| `NAME.img` (new) | disk images the disk tool reads from a drive (`U`) |
| `NAME.bad` (new) | the unreadable sectors of `NAME.img`, when there were any |

- Names are matched regardless of case, and the extension may be omitted.
- Copy `environments/disktool.m20x` (and `hello.m20x` if you like) into
  the directory.

Options:

| Option | |
|---|---|
| `--dir DIR` | the served directory (default: the current one) |
| `--baud N` | serial speed (default 9600; the ROM uses 9600) |
| `--overwrite` | let the disk tool's `U` replace an existing image |
| `--listen HOST:PORT` | wait for a TCP connection instead of a serial port (for emulators) |
| `-v` | print protocol details |
| `--version` | show the server version (placed before `serve`: `m20server.py --version`) |

On start the server prints what it serves. It then logs every request,
and the M20's report after each transfer. Stop it with Ctrl-C.

The self-tests (`python3 -m unittest server/test_m20server.py`) exercise the
whole protocol against a simulated M20.

---

## 8. Loading programs (L)

`L` without a name asks the server for its programs and shows a menu:

```
>l
Waiting for server...
1  disktool
2  hello
Load which (key, ESC cancels)? 2
Loaded 0001 regions, bytes 00000054, entry 06:4000
```

hello has the default `clear_vram` option, so the screen is cleared before
it starts. It then shows:

```
Hello from a loaded environment
(C) 2026 Salvatore Paxia

M20 Monitor - program returned
>
```

- The menu lists up to 35 programs, with keys 1-9 then A-Z.
- `L name` loads a program directly.

A program (an *execution environment*, file type `.m20x`) consists of:

- up to 16 blocks of memory contents, each for a fixed address;
- the start address, and the processor state it starts with;
- a few options, for example the screen height it starts with.

The transfer is checked in three ways:

- every 128-byte packet by its CRC (damaged packets are sent again);
- the program description by its own CRC;
- every block by a CRC **recomputed from the M20's memory after loading**.

A program runs only when all three are correct. So a block written to
memory that does not exist is caught before anything runs:

```
Region 00 CRC expected C952 got F322
Failed: CRC mismatch in memory
```

A verified program starts at once. Most programs return to the monitor when
they finish.

Any key on the M20 keyboard cancels the wait for the server. If no server
answers within about 4 seconds, the monitor prints `Failed: no server`.

---

## 9. The disk tool

The disk tool formats floppies and copies whole disks between the M20 and
disk image files on the PC. It is not in the ROM. Load it from the server:

```
>l disktool
M20 disk tool 1.0  (C) 2026 Salvatore Paxia
H for help, Q back to the monitor
disk>
```

| Command | Action |
|---|---|
| `L` | List the server's programs and disk images |
| `F dev [S]` | Format the floppy in drive `dev`, after a y/n question; `S` = single sided |
| `W dev name [F]` | Write the server's disk image `name` to drive `dev`, after a y/n question; with `F` the floppy is formatted first |
| `U dev name [count] [Rn]` | Read drive `dev` and store it on the server as `name.img`; `Rn` = extra read attempts for a failing sector (hex, default 3) |
| `H` | Help |
| `Q` | Back to the monitor |

- `dev`: `0` = floppy A, `1` = floppy B, `10` = hard disk.
- `count` (hexadecimal) is the number of 256-byte sectors to read. Without
  it, `U` reads the whole PCOS volume:

  | Disk | Default count | |
  |---|---|---|
  | 160K floppy (single sided) | 280 hex = 640 sectors | 40 tracks × 1 side × 16 |
  | 320K floppy | 460 hex = 1120 sectors | 35 tracks × 2 sides × 16 |
  | 640K floppy | A00 hex = 2560 sectors | 80 tracks × 2 sides × 16 |
  | hard disk | 8700 hex = 34560 sectors | 8.4 MB |

  The floppy's media type decides: the one recorded when the disk was
  booted from or formatted by the disk tool, otherwise the drive's type.
  PCOS formats 40 tracks on a 320K disk but its volume uses 35. A count
  of 500 hex (1280) reads all 40.

Example, copying the disk in drive A to the PC, and a PC image to the disk
in drive B:

```
disk> u 0 mybackup
Waiting for server...
0450
Sent 0460 sectors
disk> w 1 pcos41
Overwrite drive 1 with image pcos41? (y/n) y
Waiting for server...
0450
Wrote 0460 sectors
```

Example, a new (unformatted) floppy in drive B:

```
disk> w 1 pcos41 f
Format drive 1 and write image pcos41? (y/n) y
Track 39 side 1
Format complete
Waiting for server...
0450
Wrote 0460 sectors
```

Formatting follows PCOS's own VFormat exactly:

- The disk is formatted for the drive's type: 320K drives 40 tracks,
  2 sides; 640K drives 80 tracks, 2 sides; 160K drives 40 tracks, 1 side.
  `S` formats a disk in a 320K drive single sided.
- Track 0 side 0 gets 16 single-density (FM) sectors of 128 bytes; every
  other track gets 16 double-density (MFM) sectors of 256 bytes, in
  VFormat's interleaved order. All sectors are filled with zeros.
- Only the physical format is done. There is no file system on the disk
  until an image is written with `W`, or until PCOS's VFormat creates one.
- The track number during formatting is shown in decimal, as VFormat
  does.
- Any key aborts between tracks; the disk is then only partly formatted.
- Hard disks are not formatted by the disk tool. Use PCOS's VFormat, which
  also keeps the disk's list of bad sectors.

Notes:

- The number shown while copying is the sector being transferred.
- At 9600 baud a full floppy takes about 5 to 8 minutes, the hard disk
  about 2.5 hours.
- `W` without `F` writes to an **already formatted** disk.
- The image format is a plain sector-by-sector file: sector *n* at byte
  *n* × 256. It is the format of the common M20 disk image tools and
  emulators.
  - The first 16 sectors of a floppy (track 0, side 0) hold only 128 bytes
    each on the disk. In the image they fill the first half of their
    256-byte slot, and `U` sets the second half to zero.
  - The usual 320K PCOS images hold 35 cylinders × 2 sides × 16 sectors;
    hard disk images hold 180 cylinders × 6 heads × 32 sectors.
- `U` never replaces an existing file on the PC unless the server was
  started with `--overwrite`.
- **Writing** (`W`, `F`) stops at the first disk error and shows the BIOS
  error code and the sector: `BIOS error 10 at sector 0120`. The BIOS has
  already retried, recalibrating the drive, before it reports an error.
- **Reading** (`U`) does not stop at a bad sector:
  - The disk is read 16 sectors at a time. When such a read fails, those
    16 sectors are read again one by one.
  - A sector that still fails is tried `n` more times (`Rn`, default 3).
  - After that it is reported on the screen (`Bad sector 0123 (BIOS error
    10)`), filled with zeros in the image, and the upload continues.
  - At the end the list of bad sectors is sent to the server, which stores
    it next to the image as `name.bad`: one sector per line, in decimal and
    hex. The server's listing shows the number of bad sectors of each image.
  - A later clean upload of the same name removes the old `name.bad`.
  - More than 2048 bad sectors cancel the upload.
- Keys typed during a transfer are not read and may arrive garbled
  afterwards.

---

## 10. Writing your own programs

Any Z8001 program can be packaged as an environment with
`m20server.py build`:

```sh
# from an assembled and linked z8k-coff file (sections at their link addresses)
python3 server/m20server.py build -o prog.m20x --coff prog.coff

# from raw binaries, each with its load address
python3 server/m20server.py build -o prog.m20x \
        --region code.bin@6:4000 --region table.bin@6:6000 --entry 6:4000

# show what a file contains
python3 server/m20server.py info prog.m20x
```

`build` options:

| Option | |
|---|---|
| `--coff FILE` | take the loadable sections and the entry point from a z8k-coff file |
| `--region FILE@seg:off` | add a block (repeatable) |
| `--entry seg:off` | start address (default: the first block) |
| `--sp seg:off` | initial stack pointer (default: the monitor's) |
| `--psap seg:off` | program status area (default: the BIOS one) |
| `--fcw HEX` | flag/control word at start (default C000: segmented, system mode, interrupts off) |
| `--flag NAME` | add an option, repeatable (below) |
| `--no-default-flags` | start from no options instead of the defaults |

Options (`--flag`):

| Name | Effect |
|---|---|
| `screen_full` | full-height screen; without it the screen is set to the top half, as the BIOS boot does |
| `clear_vram` | clear the screen before starting |
| `ei_nvi`, `ei_vi` | enable the non-vectored / vectored interrupts at start |
| `load_regs` | load r0-r13 from the file (`build` writes zeros; other values need the Python API, `m20env.Environment(regs=…)`) |
| `return_addr` | push the monitor's return address, so a plain `ret` returns to the monitor |

The defaults are `screen_full`, `clear_vram`, `return_addr`.

Rules:

- A block must not lie in segment 4 (the ROM).
- A block must not overlap the monitor's memory `<<2>>:A000-A7FF`.
- A block must not cross the end of its segment.
- `build` refuses such a file. It also warns if a block reaches the
  monitor's memory through another segment's mapping.
- In segment 6 the M20 maps code and data to the same memory in the
  standard black-and-white configurations. `<<6>>:0000-BFFF` is a safe
  place for programs, and the example and the disk tool run there.
- The disk tool uses `<<6>>:0000-AFFF` itself while it runs.

Calling the ROM:

- The BIOS routines (screen output, keyboard, disk) are at their 2.0f
  addresses, the ROM entry table at `<<4>>:0062-00B6`.
  - `<<4>>:0080` prints the character in rl0.
  - `<<4>>:0086` prints the zero-terminated string at rr12.
- **`<<4>>:200C`** is the monitor's warm entry. Jumping there returns to
  the monitor.

The example `hello.m20x` does exactly this. It prints a line through
`<<4>>:0086`, stores `12345678` at `<<6>>:4100`, and returns.

---

## 11. Messages

| Message | Meaning |
|---|---|
| `? line` | unknown command or wrong argument |
| `Waiting for server...` | a request was sent |
| `Failed: no server` | no answer within about 4 seconds: check the cable, the server, and CTS |
| `Failed: refused by server` | the server has no such file, or refused (e.g. an existing file for `U`) |
| `Failed: transfer error` | too many damaged packets, or the server stopped |
| `Failed: bad manifest` | the program file's description is damaged or of a different version |
| `Failed: bad region (…)` | a block is empty, crosses a segment end, lies in the ROM or over the monitor's memory |
| `Failed: length mismatch` | the data received does not match the declared size |
| `Failed: CRC mismatch in memory` | a block read back from memory differs; usually memory that does not exist at that address |
| `Failed: aborted` | a key was pressed while waiting |
| `Failed: disk error` + `BIOS error xx at sector yyyy` | the BIOS reported a disk error while writing |
| `Bad sector yyyy (BIOS error xx)` | `U`: a sector could not be read; it is zero-filled and listed in `name.bad` |
| `Too many bad sectors: upload cancelled` | `U`: more than 2048 unreadable sectors |
| `Format failed: BIOS error xx` | `F`: a track could not be written |
| `Format aborted: the disk may be unusable` | `F`: a key was pressed during formatting |
| `No environments on the server` | the `L` menu is empty |

---

## 12. Limitations

- The serial speed is fixed at 9600 baud.
- The disk tool formats floppies only, not the hard disk.
- The monitor works with the keyboard and screen; there is no serial
  console.
- Booting PCOS directly from a disk image on the PC is not available. Write
  the image to a disk with the disk tool, then boot it with `B`.
- MAME's M20 driver maps only 8 KB of boot ROM, so running 2.0x under MAME
  needs a modified driver (not included).

---

## 13. Technical reference

### ROM layout

| Offset | |
|---|---|
| `0000-1FFF` | BIOS 2.0f. Changed only at: `0010-0011` ROM length 2020 (16 KB checksum); `0A8C-0A91` and `1444-1449` jumps to the key handling (same size as the replaced compares); `12E6` banner "2.0x" |
| `2000` | monitor header: `"M20MON"`, monitor version `0100` (1.0), base BIOS `020F`, `jp` warm entry at `200C` |
| `2020-` | monitor |
| `3FFE` | checksum fixup word |

The monitor's memory is `<<2>>:A000-A7FF`.

### Serial protocol

Requests from the M20 have the form `<letter> <length> <name> [extra]
<XOR>`, the XOR covering every byte sent. Data then follows as XMODEM-CRC:
128-byte packets, CRC-16/XMODEM, NAK and resend, ten errors abort, EOT at
the end, block numbers wrapping from 255 to 0.

| Request | Server answer |
|---|---|
| `X name` | CAN (not found), or ACK and the program; the M20 then sends `R <status> <XOR>` |
| `E` | ACK and the program names, one per line |
| `D` | ACK and the listing text |
| `I name` | CAN, or ACK + sector count (4 bytes, big-endian) + their XOR and the image; the M20 then sends `R <status> <XOR>` |
| `P name count(4 bytes)` | CAN, or ACK; the M20 then sends the image |
| `B name dev(1) count(2 bytes)` | after `P`, if sectors could not be read: CAN, or ACK; the M20 then sends the sector numbers (2 bytes each), stored as `name.bad` |

Status codes: 0 ok, 1 no server, 2 refused, 3 transfer error, 4 bad
manifest, 5 bad region, 6 length mismatch, 7 CRC mismatch in memory,
8 aborted, 9 disk error.

### Program file (`.m20x`)

A 256-byte description, then the blocks' contents in order. All values are
big-endian. Addresses are Z8001 segmented long addresses: `8S00oooo` =
segment S, offset oooo.

| Offset | Size | |
|---|---|---|
| 00 | 4 | `M20X` |
| 04 | 2 | version 1 |
| 06 | 2 | description length, 256 |
| 08 | 2 | options (bits: 0 reserved, 1 screen_full, 2 clear_vram, 3 ei_nvi, 4 ei_vi, 5 load_regs, 6 return_addr) |
| 0A | 2 | number of blocks, 1-16 |
| 0C | 4 | total bytes of all blocks |
| 10 | 4 | start address |
| 14 | 2 | flag/control word |
| 16 | 2 | reserved |
| 18 | 4 | stack pointer, 0 = monitor's |
| 1C | 4 | program status area, 0 = BIOS's |
| 20 | 28 | r0-r13 |
| 3C | 2 | CRC-16/XMODEM of these 256 bytes, computed with this field zero |
| 3E | 2 | reserved |
| 40 | 16 × 12 | blocks: address (4), length (4), CRC-16 of the contents (2), reserved (2) |

---

## 14. License

The Python server code in `server/` (`m20server.py`, `m20env.py`,
`m20link.py`, `test_m20server.py`) is Copyright (C) 2026 Salvatore Paxia.

It is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. It is distributed WITHOUT ANY WARRANTY; see [LICENSE](LICENSE) for
the full text.

The ROM images contain the original Olivetti BIOS 2.0f in their lower 8 KB.
