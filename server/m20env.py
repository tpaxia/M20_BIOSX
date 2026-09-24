# Copyright (C) 2026 Salvatore Paxia
# SPDX-License-Identifier: GPL-3.0-or-later
"""m20env.py — M20X execution environments for the M20 ROM monitor loader.

Stream layout (big-endian, as the Z8001):

  manifest, 256 bytes
    0x00 "M20X"                 0x14 FCW (u16)
    0x04 version = 1 (u16)      0x16 reserved
    0x06 manifest length = 256  0x18 initial rr14, 0 = monitor default (u32)
    0x08 flags (u16)            0x1C PSAP, 0 = keep the BIOS PSAP (u32)
    0x0A region count 1-16      0x20 r0-r13 (14 x u16)
    0x0C payload bytes (u32)    0x3C CRC-16/XMODEM of the manifest, field = 0
    0x10 entry PC (u32)         0x3E reserved
    0x40 16 regions x {dest u32, length u32, crc16 u16, flags u16 = 0}
  region data, concatenated in table order

Addresses are Z8001 segmented longs: 0x8S00_oooo = <<S>>:oooo.
Flag bit 0 is reserved (formerly "autorun": the monitor now always runs a
verified environment).
Must match monitor/include/monitor.inc and monitor/src/loader.s.
"""

import struct

MAGIC = b"M20X"
VERSION = 1
MANIFEST_SIZE = 256
MAX_REGIONS = 16
REGION_FMT = ">IIHH"
HEADER_FMT = ">4sHHHHIIHHII14H"          # up to 0x3C
CRC_OFFSET = 0x3C

FLAGS = {
    "screen_full": 1 << 1,
    "clear_vram": 1 << 2,
    "ei_nvi": 1 << 3,
    "ei_vi": 1 << 4,
    "load_regs": 1 << 5,
    "return_addr": 1 << 6,
}
KNOWN_FLAGS = sum(FLAGS.values())
DEFAULT_FLAGS = FLAGS["screen_full"] | FLAGS["clear_vram"] | FLAGS["return_addr"]
DEFAULT_FCW = 0xC000                        # segmented, system, interrupts off

# ROM monitor RAM (config.mk MON_RAM, 0x800 bytes including its stack).
MONITOR_RAM = (0x8200A000, 0x800)

# B/W 512K mapping table, data space, from docs/hardware.md:
# (segment, 16K window) -> (device, physical offset) or None.
BW512_DATA = {
    (0, 0): ("DRAM0", 0x04000), (0, 1): ("DRAM1", 0x04000), (0, 2): ("DRAM2", 0x00000), (0, 3): ("DRAM2", 0x04000),
    (1, 0): ("DRAM0", 0x14000), (1, 1): ("DRAM0", 0x18000), (1, 2): ("DRAM0", 0x1C000), (1, 3): ("DRAM1", 0x00000),
    (2, 0): ("DRAM0", 0x14000), (2, 1): ("DRAM0", 0x18000), (2, 2): ("DRAM0", 0x1C000), (2, 3): ("DRAM1", 0x00000),
    (3, 0): ("DRAM0", 0x00000), (3, 1): None, (3, 2): None, (3, 3): None,
    (4, 0): ("ROM0", 0x00000), (4, 1): ("DRAM3", 0x00000), (4, 2): ("DRAM3", 0x04000), (4, 3): None,
    (5, 0): ("DRAM0", 0x08000), (5, 1): ("DRAM0", 0x0C000), (5, 2): ("DRAM0", 0x10000), (5, 3): ("SRAM0", 0x00000),
    (6, 0): ("DRAM0", 0x08000), (6, 1): ("DRAM0", 0x0C000), (6, 2): ("DRAM0", 0x10000), (6, 3): None,
    (7, 0): ("ROM0", 0x00000), (7, 1): ("ROM0", 0x10000), (7, 2): ("ROM0", 0x14000), (7, 3): ("ROM0", 0x18000),
    (8, 0): ("DRAM0", 0x18000), (8, 1): ("DRAM0", 0x1C000), (8, 2): ("DRAM1", 0x0C000), (8, 3): ("DRAM1", 0x10000),
    (9, 0): ("DRAM0", 0x18000), (9, 1): ("DRAM0", 0x1C000), (9, 2): ("DRAM1", 0x0C000), (9, 3): ("DRAM1", 0x10000),
    (10, 0): ("DRAM0", 0x08000), (10, 1): ("DRAM0", 0x0C000), (10, 2): ("DRAM1", 0x04000), (10, 3): ("DRAM1", 0x08000),
    (11, 0): ("DRAM1", 0x14000), (11, 1): ("DRAM1", 0x18000), (11, 2): ("DRAM1", 0x1C000), (11, 3): ("DRAM2", 0x00000),
    (12, 0): ("DRAM2", 0x04000), (12, 1): ("DRAM2", 0x08000), (12, 2): ("DRAM2", 0x0C000), (12, 3): ("DRAM2", 0x10000),
    (13, 0): ("DRAM2", 0x14000), (13, 1): ("DRAM2", 0x18000), (13, 2): ("DRAM2", 0x1C000), (13, 3): ("DRAM3", 0x00000),
    (14, 0): ("DRAM3", 0x04000), (14, 1): ("DRAM3", 0x08000), (14, 2): ("DRAM3", 0x0C000), (14, 3): ("DRAM3", 0x10000),
    (15, 0): ("DRAM3", 0x14000), (15, 1): ("DRAM3", 0x18000), (15, 2): ("DRAM3", 0x1C000), (15, 3): ("DRAM3", 0x00000),
}


class EnvError(ValueError):
    pass


def crc16_xmodem(data, crc=0):
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def parse_addr(text):
    """'ss:oooo' (hex) or a 32-bit number -> segmented long 0x8Sss_oooo."""
    text = text.strip()
    if ":" in text:
        seg, off = text.split(":", 1)
        seg, off = int(seg, 16), int(off or "0", 16)
    else:
        value = int(text, 0)
        seg, off = (value >> 24) & 0x7F, value & 0xFFFF
    if not 0 <= seg <= 0x7F or not 0 <= off <= 0xFFFF:
        raise EnvError(f"bad address {text!r}")
    return 0x80000000 | (seg << 24) | off


def fmt_addr(addr):
    return f"{(addr >> 24) & 0x7F:02X}:{addr & 0xFFFF:04X}"


def segment(addr):
    return (addr >> 24) & 0x7F


def offset(addr):
    return addr & 0xFFFF


class Environment:
    def __init__(self, entry, regions, flags=DEFAULT_FLAGS, fcw=DEFAULT_FCW,
                 sp=0, psap=0, regs=None):
        self.entry = entry
        self.regions = regions            # [(dest, bytes)]
        self.flags = flags
        self.fcw = fcw
        self.sp = sp
        self.psap = psap
        self.regs = list(regs or [0] * 14)

    # ---------------------------------------------------------------- checks
    def check(self):
        """Raise EnvError for anything the M20 loader would refuse."""
        if not 1 <= len(self.regions) <= MAX_REGIONS:
            raise EnvError(f"{len(self.regions)} regions (1-{MAX_REGIONS} allowed)")
        if self.flags & ~KNOWN_FLAGS:
            raise EnvError(f"unknown flag bits 0x{self.flags & ~KNOWN_FLAGS:04x}")
        if len(self.regs) != 14:
            raise EnvError("need 14 register values (r0-r13)")
        mon_seg, mon_off = segment(MONITOR_RAM[0]), offset(MONITOR_RAM[0])
        for dest, data in self.regions:
            where = fmt_addr(dest)
            if not data:
                raise EnvError(f"region {where}: empty")
            if offset(dest) + len(data) > 0x10000:
                raise EnvError(f"region {where}: crosses the end of segment "
                               f"{segment(dest):X} (split it per segment)")
            if segment(dest) == 4:
                raise EnvError(f"region {where}: segment 4 is the ROM")
            if segment(dest) == mon_seg and offset(dest) < mon_off + MONITOR_RAM[1] \
                    and offset(dest) + len(data) > mon_off:
                raise EnvError(f"region {where}: overlaps the monitor RAM "
                               f"{fmt_addr(MONITOR_RAM[0])}-"
                               f"{mon_off + MONITOR_RAM[1] - 1:04X}")

    def alias_warnings(self):
        """Regions that reach the monitor RAM's physical memory through
        another segment, per the B/W 512K mapping table.  Advisory only:
        the mapping depends on the memory/display configuration."""
        def physical(addr):
            window = BW512_DATA.get((segment(addr) & 0x0F, offset(addr) >> 14))
            if window is None:
                return None
            return window[0], window[1] + (offset(addr) & 0x3FFF)

        mon = [physical(MONITOR_RAM[0] + i) for i in (0, MONITOR_RAM[1] - 1)]
        warnings = []
        for dest, data in self.regions:
            if segment(dest) == segment(MONITOR_RAM[0]):
                continue
            for i in range(0, len(data), 0x100):
                p = physical(dest + i)
                if p and mon[0] and p[0] == mon[0][0] and mon[0][1] <= p[1] <= mon[1][1]:
                    warnings.append(f"region {fmt_addr(dest)} +{i:04X} maps to "
                                    f"{p[0]} {p[1]:05X}, the monitor RAM (B/W 512K table)")
                    break
        return warnings

    # --------------------------------------------------------------- encoding
    def manifest(self):
        self.check()
        total = sum(len(d) for _, d in self.regions)
        header = struct.pack(HEADER_FMT, MAGIC, VERSION, MANIFEST_SIZE,
                             self.flags, len(self.regions), total, self.entry,
                             self.fcw, 0, self.sp, self.psap, *self.regs)
        table = b"".join(struct.pack(REGION_FMT, dest, len(data),
                                     crc16_xmodem(data), 0)
                         for dest, data in self.regions)
        block = bytearray(header + b"\0\0\0\0" + table)
        block.extend(b"\0" * (MANIFEST_SIZE - len(block)))
        struct.pack_into(">H", block, CRC_OFFSET, crc16_xmodem(block))
        return bytes(block)

    def stream(self):
        return self.manifest() + b"".join(data for _, data in self.regions)

    @classmethod
    def from_stream(cls, stream):
        if len(stream) < MANIFEST_SIZE:
            raise EnvError("shorter than the manifest")
        block = bytearray(stream[:MANIFEST_SIZE])
        (magic, version, length, flags, count, total, entry, fcw, _res,
         sp, psap, *regs) = struct.unpack_from(HEADER_FMT, block)
        crc = struct.unpack_from(">H", block, CRC_OFFSET)[0]
        struct.pack_into(">H", block, CRC_OFFSET, 0)
        if magic != MAGIC or version != VERSION or length != MANIFEST_SIZE:
            raise EnvError("not an M20X version 1 environment")
        if crc16_xmodem(block) != crc:
            raise EnvError("manifest CRC mismatch")
        pos = MANIFEST_SIZE
        regions = []
        for i in range(count):
            dest, size, rcrc, _ = struct.unpack_from(REGION_FMT, block, 0x40 + 12 * i)
            data = bytes(stream[pos:pos + size])
            if len(data) != size or crc16_xmodem(data) != rcrc:
                raise EnvError(f"region {i} data missing or CRC mismatch")
            regions.append((dest, data))
            pos += size
        if pos - MANIFEST_SIZE != total:
            raise EnvError("payload total mismatch")
        return cls(entry, regions, flags, fcw, sp, psap, regs)

    def describe(self):
        names = [n for n, bit in FLAGS.items() if self.flags & bit] or ["none"]
        lines = [f"entry {fmt_addr(self.entry)}  FCW {self.fcw:04X}  "
                 f"SP {fmt_addr(self.sp) if self.sp else 'monitor'}  "
                 f"PSAP {fmt_addr(self.psap) if self.psap else 'BIOS'}",
                 f"flags {', '.join(names)}"]
        for dest, data in self.regions:
            lines.append(f"  {fmt_addr(dest)}  {len(data):5d} bytes  "
                         f"CRC {crc16_xmodem(data):04X}")
        return "\n".join(lines)
