#!/usr/bin/env python3
# Copyright (C) 2026 Salvatore Paxia
# SPDX-License-Identifier: GPL-3.0-or-later
"""m20server.py 1.0 — host server for the M20 BIOS 2.0x monitor.

Serves a directory over the M20 serial port (or a MAME socket):
  NAME.m20x   execution environments   monitor: L name, or L alone for a menu
  NAME.img    raw disk images          disk tool (L disktool):
                                         W dev name   (image -> drive)
                                         U dev name   (drive -> NAME.img)
                                         F            (list everything)

  m20server.py serve [--dir DIR] (--listen HOST:PORT | DEVICE) [--baud 9600]
                     [--overwrite] [--fault SPEC] [-v]
  m20server.py build -o OUT.m20x (--coff FILE | --region FILE@ss:oooo ...)
                     [--entry ss:oooo] [--sp ss:oooo] [--psap ss:oooo]
                     [--fcw HEX] [--flag NAME ...] [--no-default-flags]
  m20server.py info  FILE.m20x

Requests (M20 -> host), framed as <cmd> <len> <name> [extra]
<XOR>, the XOR covering every byte:
  X name           CAN, or ACK + XMODEM-CRC environment; then M20 'R' st XOR
  E                ACK + XMODEM-CRC environment names, one per line (menu)
  D                ACK + XMODEM-CRC listing text
  I name           CAN, or ACK + sectors(4) + XOR + XMODEM-CRC image;
                   then M20 'R' <status> <XOR>
  P name sectors(4)  CAN, or ACK and the M20 sends the image (XMODEM-CRC)
  B name dev(1) count(2)
                   after P, when sectors could not be read: CAN, or ACK and
                   the M20 sends <count> sector numbers (2 bytes each,
                   big-endian) as XMODEM-CRC; stored as NAME.bad
Names are matched case-insensitively; the extension may be omitted.
Disk images are raw LBA, 256 bytes per sector (m20disk / MAME layout).

The serial transport (Link, XMODEM-CRC send/receive) is m20link.py; pyserial
is needed for a real serial port.

--fault SPEC (testing): "corrupt:N" damages the first transmission of data
packet N (1-based) sent by the server; "corrupt:N:K" damages it K times.
"""

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import m20env                                              # noqa: E402
from m20env import Environment, EnvError, parse_addr       # noqa: E402
import m20link                                             # noqa: E402

VERSION = "1.0"

ACK, CAN = m20link.ACK, m20link.CAN
FRAME_SIZE = 3 + m20link.PACKET_SIZE + 2
SECTOR = 256
MAX_SECTORS = 0xFFFF
STATUS = {0: "ok", 1: "no server", 2: "refused", 3: "transfer error",
          4: "bad manifest", 5: "bad region", 6: "length mismatch",
          7: "CRC mismatch in memory", 8: "aborted", 9: "disk error"}
EOL = "\r\n"


def read_file(path):
    with open(path, "rb") as f:
        return f.read()


def write_file(path, data):
    with open(path, "wb") as f:
        f.write(data)


class FaultLink:
    """Wraps a Link and damages chosen XMODEM frames on their way out."""

    def __init__(self, link, packet, times):
        self._link = link
        self._packet = packet
        self._times = times
        self._frames = 0
        self._last = None

    def write(self, data, pace=0.0):
        if len(data) == FRAME_SIZE:
            if data != self._last:
                self._frames += 1
                self._last = data
            if self._frames == self._packet and self._times > 0:
                self._times -= 1
                data = bytearray(data)
                data[10] ^= 0xFF
                data = bytes(data)
        self._link.write(data, pace)

    def __getattr__(self, name):
        return getattr(self._link, name)


# -------------------------------------------------------------------- store

class Store:
    """The served directory."""

    NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

    def __init__(self, path, overwrite=False):
        self.path = path
        self.overwrite = overwrite

    def _files(self, ext):
        return sorted(f for f in os.listdir(self.path)
                      if f.lower().endswith(ext) and
                      os.path.isfile(os.path.join(self.path, f)))

    def find(self, name, ext):
        """Path of NAME or NAME+ext (case-insensitive), or None."""
        want = {name.lower(), name.lower() + ext}
        for f in self._files(ext):
            if f.lower() in want:
                return os.path.join(self.path, f)
        return None

    def new_image_path(self, name):
        """Path to store an uploaded image, or raise ValueError."""
        if not self.NAME_RE.match(name) or ".." in name:
            raise ValueError(f"bad name {name!r}")
        if not name.lower().endswith(".img"):
            name += ".img"
        existing = self.find(name, ".img")
        if existing and not self.overwrite:
            raise ValueError(f"{os.path.basename(existing)} exists (use --overwrite)")
        return existing or os.path.join(self.path, name)

    def env_names(self, limit=35):
        """Valid environments for the monitor's L menu (keys 1-9, A-Z)."""
        names = []
        for f in self._files(".m20x"):
            try:
                Environment.from_stream(read_file(os.path.join(self.path, f)))
            except (OSError, EnvError) as err:
                print(f"  menu: skipping {f}: {err}", flush=True)
                continue
            names.append(f[:-5])
        return names[:limit]

    def bad_path(self, image_path):
        """NAME.bad next to NAME.img."""
        return image_path[:-4] + ".bad"

    def bad_count(self, image_path):
        try:
            with open(self.bad_path(image_path)) as f:
                return sum(1 for line in f if line.strip() and not line.startswith("#"))
        except OSError:
            return 0

    def listing(self):
        lines = ["Environments (L name):"]
        envs = self._files(".m20x")
        for f in envs:
            size = os.path.getsize(os.path.join(self.path, f))
            lines.append(f"  {f[:-5]:<24}{size:>8} bytes")
        if not envs:
            lines.append("  none")
        lines.append("Disk images (W dev name):")
        imgs = self._files(".img")
        for f in imgs:
            size = os.path.getsize(os.path.join(self.path, f))
            note = f"{size // SECTOR:>6} sectors  {size // 1024}K"
            if size % SECTOR:
                note = "  not a multiple of 256 bytes"
            bad = self.bad_count(os.path.join(self.path, f))
            if bad:
                note += f"  {bad} bad"
            lines.append(f"  {f[:-4]:<24}{note}")
        if not imgs:
            lines.append("  none")
        return EOL.join(lines) + EOL


# ------------------------------------------------------------------- server

def read_request(link, verbose):
    """Wait for a request.  Returns (cmd, name, sectors) or None."""
    while True:
        byte = link.read_byte(timeout=3600.0)
        if byte is None:
            return None
        if byte in b"XEDIPB":
            break
        if verbose:
            shown = chr(byte) if 32 <= byte < 127 else f"0x{byte:02x}"
            print(f"  ignoring {shown}", flush=True)
    cmd = chr(byte)
    checksum = byte
    length = link.read_byte(timeout=2.0)
    if length is None:
        return None
    checksum ^= length
    raw = bytearray()
    extra = {"P": 4, "B": 3}.get(cmd, 0)
    for _ in range(length + extra):
        b = link.read_byte(timeout=2.0)
        if b is None:
            return None
        checksum ^= b
        raw.append(b)
    received = link.read_byte(timeout=2.0)
    if received != checksum:
        print(f"bad request checksum (got {received}, expected {checksum})", flush=True)
        return None
    name = raw[:length].decode("ascii", errors="replace")
    if cmd == "P":
        return cmd, name, int.from_bytes(raw[length:], "big")
    if cmd == "B":
        return cmd, name, (raw[length], int.from_bytes(raw[length + 1:], "big"))
    return cmd, name, None


def read_status(link, timeout=60.0):
    """'R' <status> <XOR> from the M20."""
    while True:
        b = link.read_byte(timeout=timeout)
        if b is None:
            return None
        if b == ord("R"):
            break
    status = link.read_byte(timeout=2.0)
    check = link.read_byte(timeout=2.0)
    if status is None or check != (status ^ ord("R")):
        return None
    return status


def serve_env(link, store, name, out):
    path = store.find(name, ".m20x") if name else None
    print(f"environment {name!r} -> {path or 'not found'}", flush=True)
    if not path:
        link.write(bytes([CAN]))
        return
    try:
        stream = Environment.from_stream(read_file(path)).stream()
    except EnvError as err:
        print(f"  cannot serve: {err}", flush=True)
        link.write(bytes([CAN]))
        return
    link.write(bytes([ACK]))
    if m20link.send_stream(out, stream, verbose=out.verbose):
        print(f"  sent {len(stream)} bytes", flush=True)
    else:
        print("  transfer failed", flush=True)
    status = read_status(link)
    print(f"  M20 reports: {STATUS.get(status, status)}", flush=True)


def serve_names(link, store, out):
    names = store.env_names()
    print(f"menu: {', '.join(names) or 'no environments'}", flush=True)
    link.write(bytes([ACK]))
    m20link.send_stream(out, "".join(n + EOL for n in names), verbose=out.verbose)


def serve_list(link, store, out):
    print("listing", flush=True)
    link.write(bytes([ACK]))
    m20link.send_stream(out, store.listing(), verbose=out.verbose)


def serve_image(link, store, name, out):
    path = store.find(name, ".img")
    print(f"disk image {name!r} -> {path or 'not found'}", flush=True)
    data = read_file(path) if path else b""
    if not data or len(data) % SECTOR or len(data) // SECTOR > MAX_SECTORS:
        if path:
            print(f"  refusing: {len(data)} bytes is not 1-{MAX_SECTORS} "
                  f"sectors of {SECTOR}", flush=True)
        link.write(bytes([CAN]))
        return
    count = (len(data) // SECTOR).to_bytes(4, "big")
    x = 0
    for b in count:
        x ^= b
    link.write(bytes([ACK]) + count + bytes([x]))
    print(f"  {len(data) // SECTOR} sectors", flush=True)
    if m20link.send_stream(out, data, verbose=out.verbose):
        print(f"  sent {len(data)} bytes", flush=True)
    else:
        print("  transfer failed", flush=True)
    status = read_status(link, timeout=120.0)
    print(f"  M20 reports: {STATUS.get(status, status)}", flush=True)


def serve_upload(link, store, name, sectors, verbose):
    print(f"upload {name!r}, {sectors} sectors", flush=True)
    try:
        if not 1 <= sectors <= MAX_SECTORS:
            raise ValueError(f"{sectors} sectors")
        path = store.new_image_path(name)
    except ValueError as err:
        print(f"  refusing: {err}", flush=True)
        link.write(bytes([CAN]))
        return
    data = m20link.recv_stream(link, sectors * SECTOR, verbose)   # sends the ACK
    if data is None or len(data) != sectors * SECTOR:
        print("  transfer failed", flush=True)
        return
    write_file(path, data)
    print(f"  stored {path} ({len(data)} bytes)", flush=True)
    stale = store.bad_path(path)
    if os.path.exists(stale):                 # a list from an earlier upload
        os.unlink(stale)
        print(f"  removed old {stale}", flush=True)


def serve_badlist(link, store, name, dev, count, verbose):
    """The M20 could not read <count> sectors of the image it just sent."""
    print(f"bad sectors for {name!r}: {count}", flush=True)
    image = store.find(name, ".img")
    if not image or not 1 <= count <= MAX_SECTORS:
        print("  refusing: no such uploaded image", flush=True)
        link.write(bytes([CAN]))
        return
    data = m20link.recv_stream(link, count * 2, verbose)      # sends the ACK
    if data is None or len(data) != count * 2:
        print("  transfer failed", flush=True)
        return
    sectors = [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data), 2)]
    device = {0: "floppy A", 1: "floppy B", 10: "hard disk"}.get(dev, f"device {dev}")
    lines = [f"# {os.path.basename(image)}: {count} sector(s) of {device} could not be read.",
             "# They are filled with zeros in the image. One sector per line:",
             "# decimal number, hex number (sector n is at byte n * 256 of the image)."]
    lines += [f"{n} 0x{n:04X}" for n in sectors]
    path = store.bad_path(image)
    write_file(path, ("\n".join(lines) + "\n").encode())
    print(f"  stored {path}", flush=True)


class Out:
    """The link data is sent through (a FaultLink when testing)."""

    def __init__(self, link, fault, verbose):
        self._target = FaultLink(link, *fault) if fault else link
        self.verbose = verbose

    def write(self, data, pace=0.0):
        self._target.write(data, pace)

    def read_byte(self, timeout):
        return self._target.read_byte(timeout)


def serve(link, store, fault=None, verbose=False):
    out = Out(link, fault, verbose)
    while True:
        request = read_request(link, verbose)
        if request is None:
            continue
        cmd, name, sectors = request
        if cmd == "X":
            serve_env(link, store, name, out)
        elif cmd == "E":
            serve_names(link, store, out)
        elif cmd == "D":
            serve_list(link, store, out)
        elif cmd == "I":
            serve_image(link, store, name, out)
        elif cmd == "P":
            serve_upload(link, store, name, sectors, verbose)
        elif cmd == "B":
            dev, count = sectors
            serve_badlist(link, store, name, dev, count, verbose)


# -------------------------------------------------------------------- build

def coff_regions(path):
    """Loadable sections of a z8k-coff file -> [(segmented dest, bytes)], entry."""
    out = subprocess.run(["z8k-coff-objdump", "-h", "-f", path], check=True,
                         capture_output=True, text=True).stdout
    lines = out.splitlines()
    entry = None
    sections = []
    for i, line in enumerate(lines):
        if line.startswith("start address"):
            entry = int(line.split()[-1], 16)
        parts = line.split()
        if len(parts) >= 7 and parts[0].isdigit():
            name, size, lma = parts[1], int(parts[2], 16), int(parts[4], 16)
            flags = lines[i + 1] if i + 1 < len(lines) else ""
            if size and "LOAD" in flags:
                sections.append((name, lma, size))
    regions = []
    for name, lma, size in sections:
        tmp = path + "." + name.strip(".") + ".bin"
        subprocess.run(["z8k-coff-objcopy", "-O", "binary", "-j", name, path, tmp],
                       check=True)
        data = read_file(tmp)
        os.unlink(tmp)
        regions.append((linear_to_seg(lma), data))
    return regions, linear_to_seg(entry) if entry is not None else None


def linear_to_seg(linear):
    return 0x80000000 | ((linear >> 16) & 0x7F) << 24 | (linear & 0xFFFF)


def cmd_build(args):
    if args.coff:
        regions, entry = coff_regions(args.coff)
    else:
        regions, entry = [], None
    for spec in args.region:
        path, _, addr = spec.rpartition("@")
        regions.append((parse_addr(addr), read_file(path)))
    if args.entry:
        entry = parse_addr(args.entry)
    if entry is None:
        if not regions:
            sys.exit("nothing to load")
        entry = regions[0][0]
    flags = 0 if args.no_default_flags else m20env.DEFAULT_FLAGS
    for name in args.flag:
        if name not in m20env.FLAGS:
            sys.exit(f"unknown flag {name}; known: {', '.join(m20env.FLAGS)}")
        flags |= m20env.FLAGS[name]
    env = Environment(entry, regions, flags, int(args.fcw, 16),
                      parse_addr(args.sp) if args.sp else 0,
                      parse_addr(args.psap) if args.psap else 0)
    try:
        stream = env.stream()
    except EnvError as err:
        sys.exit(f"error: {err}")
    for warning in env.alias_warnings():
        print(f"warning: {warning}")
    write_file(args.output, stream)
    print(f"{args.output}: {len(stream)} bytes")
    print(env.describe())


def cmd_info(args):
    try:
        env = Environment.from_stream(read_file(args.file))
    except EnvError as err:
        sys.exit(f"{args.file}: {err}")
    print(env.describe())
    for warning in env.alias_warnings():
        print(f"warning: {warning}")


def cmd_serve(args):
    if not os.path.isdir(args.dir):
        sys.exit(f"{args.dir}: not a directory")
    store = Store(args.dir, args.overwrite)
    print(f"m20server {VERSION} for M20 BIOS 2.0x")
    fault = None
    if args.fault:
        kind, *nums = args.fault.split(":")
        if kind != "corrupt" or not nums:
            sys.exit("--fault corrupt:N[:K]")
        fault = (int(nums[0]), int(nums[1]) if len(nums) > 1 else 1)
    print(store.listing().replace(EOL, "\n"), end="")
    if args.listen:
        link = m20link.Link.listen(args.listen)
    elif args.device:
        link = m20link.Link.open_serial(args.device, args.baud)
    else:
        sys.exit("give a serial device or --listen HOST:PORT")
    try:
        serve(link, store, fault, args.verbose)
    except (ConnectionResetError, KeyboardInterrupt) as err:
        print(f"stopped: {err or 'interrupted'}")
    finally:
        link.close()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version",
                   version=f"m20server {VERSION} (C) 2026 Salvatore Paxia, GPL-3.0-or-later")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve")
    s.add_argument("device", nargs="?")
    s.add_argument("--dir", default=".", help="served directory (default .)")
    s.add_argument("--listen", metavar="HOST:PORT")
    s.add_argument("--baud", type=int, default=9600)
    s.add_argument("--overwrite", action="store_true",
                   help="let U replace an existing image")
    s.add_argument("--fault", metavar="SPEC")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("build")
    s.add_argument("-o", "--output", required=True)
    s.add_argument("--coff")
    s.add_argument("--region", action="append", default=[], metavar="FILE@ss:oooo")
    s.add_argument("--entry")
    s.add_argument("--sp")
    s.add_argument("--psap")
    s.add_argument("--fcw", default=f"{m20env.DEFAULT_FCW:04X}")
    s.add_argument("--flag", action="append", default=[])
    s.add_argument("--no-default-flags", action="store_true")
    s.set_defaults(func=cmd_build)

    s = sub.add_parser("info")
    s.add_argument("file")
    s.set_defaults(func=cmd_info)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
