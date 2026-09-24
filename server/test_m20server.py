# Copyright (C) 2026 Salvatore Paxia
# SPDX-License-Identifier: GPL-3.0-or-later
"""Host tests for the M20 monitor server (no hardware, no MAME).

    cd monitor && python3 -m unittest -v host/test_m20server.py
"""

import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import m20env                                    # noqa: E402
from m20env import Environment, EnvError, crc16_xmodem, parse_addr  # noqa: E402
import m20server                                 # noqa: E402
import m20link                                   # noqa: E402

SOH, EOT, ACK, NAK, CAN = 0x01, 0x04, 0x06, 0x15, 0x18

# Keep the server's log quiet.  Shadowing print in its module (rather than
# swapping sys.stdout per server thread) is safe with several server threads
# alive at once.
m20server.print = lambda *args, **kwargs: None


def env(regions, **kw):
    return Environment(regions[0][0], regions, **kw)


class Format(unittest.TestCase):
    def test_crc_check_value(self):
        self.assertEqual(crc16_xmodem(b"123456789"), 0x31C3)

    def test_parse_addr(self):
        self.assertEqual(parse_addr("6:4000"), 0x86004000)
        self.assertEqual(parse_addr("0A:"), 0x8A000000)
        self.assertEqual(parse_addr("0x86004000"), 0x86004000)

    def test_manifest_layout(self):
        e = env([(parse_addr("6:4000"), b"\x11\x22\x33")], sp=parse_addr("6:8000"))
        m = e.manifest()
        self.assertEqual(len(m), 256)
        self.assertEqual(m[0:4], b"M20X")
        self.assertEqual(struct.unpack_from(">HHHH", m, 4), (1, 256, e.flags, 1))
        self.assertEqual(struct.unpack_from(">II", m, 0x0C), (3, 0x86004000))
        self.assertEqual(struct.unpack_from(">H", m, 0x14)[0], 0xC000)
        self.assertEqual(struct.unpack_from(">I", m, 0x18)[0], 0x86008000)
        self.assertEqual(struct.unpack_from(">IIHH", m, 0x40),
                         (0x86004000, 3, crc16_xmodem(b"\x11\x22\x33"), 0))
        block = bytearray(m)
        crc = struct.unpack_from(">H", block, 0x3C)[0]
        struct.pack_into(">H", block, 0x3C, 0)
        self.assertEqual(crc16_xmodem(block), crc)

    def test_round_trip(self):
        e = env([(parse_addr("6:0"), bytes(range(256)) * 5),
                 (parse_addr("3:100"), b"abc")],
                flags=m20env.FLAGS["load_regs"], regs=list(range(14)))
        back = Environment.from_stream(e.stream())
        self.assertEqual(back.regions, e.regions)
        self.assertEqual((back.flags, back.regs, back.entry),
                         (e.flags, e.regs, e.entry))

    def test_rejections(self):
        bad = [
            [(parse_addr("6:0"), b"")],                       # empty
            [(parse_addr("4:2000"), b"x")],                   # ROM
            [(parse_addr("2:A7FF"), b"x")],                   # monitor RAM
            [(parse_addr("2:9FFF"), b"xy")],                  # monitor RAM
            [(parse_addr("6:FFFF"), b"xy")],                  # segment end
            [(parse_addr("6:0"), b"x")] * 17,                 # too many
        ]
        for regions in bad:
            with self.assertRaises(EnvError):
                env(regions).manifest()
        for bad_flags in (0x8000, 0x0001):                   # unknown / reserved bits
            with self.assertRaises(EnvError):
                env([(parse_addr("6:0"), b"x")], flags=bad_flags).manifest()
        env([(parse_addr("2:9FFF"), b"x")]).manifest()        # just below: fine
        env([(parse_addr("2:A800"), b"x")]).manifest()        # just above: fine

    def test_corrupt_stream(self):
        s = bytearray(env([(parse_addr("6:0"), b"hello")]).stream())
        s[-1] ^= 1
        with self.assertRaises(EnvError):
            Environment.from_stream(bytes(s))

    def test_alias_warning(self):
        # <<1>>:A000 is the same DRAM0 1E000 as the monitor RAM <<2>>:A000.
        e = env([(parse_addr("1:A000"), b"x" * 16)])
        self.assertTrue(e.alias_warnings())
        self.assertFalse(env([(parse_addr("6:0"), b"x")]).alias_warnings())


class FakeM20:
    """The M20 side of the protocol, as src/server.s, xmodem.s, loader.s and
    cmd_disk.s implement it."""

    def __init__(self, sock):
        self.sock = sock
        sock.settimeout(1.0)

    def byte(self):
        data = self.sock.recv(1)
        if not data:
            raise ConnectionError
        return data[0]

    def request(self, cmd, name, extra=b""):
        req = cmd.encode() + bytes([len(name)]) + name.encode() + extra
        x = 0
        for b in req:
            x ^= b
        self.sock.sendall(req + bytes([x]))

    def report(self, status):
        self.sock.sendall(bytes([ord("R"), status, status ^ ord("R")]))

    def recv_xmodem(self):
        """xm_recv: returns (data, naks) or (None, naks)."""
        data = bytearray()
        expected, errors, naks = 1, 0, 0
        while True:
            try:
                first = self.byte()
            except socket.timeout:
                first = None                 # a timeout counts as an error
            if first is None:
                errors += 1
                if errors > 10:
                    return None, naks
                self.sock.sendall(bytes([NAK]))
                continue
            if first == EOT:
                self.sock.sendall(bytes([ACK]))
                return bytes(data), naks
            if first == CAN:
                return None, naks
            frame = bytes([first]) + b"".join(bytes([self.byte()]) for _ in range(132))
            blk, cblk, payload = frame[1], frame[2], frame[3:131]
            crc = frame[131] << 8 | frame[132]
            if blk ^ cblk != 0xFF or crc16_xmodem(payload) != crc:
                errors += 1
                naks += 1
                if errors > 10:
                    return None, naks
                self.sock.sendall(bytes([NAK]))
                continue
            if blk == expected:
                data.extend(payload)
                expected = (expected + 1) & 0xFF
            errors = 0
            self.sock.sendall(bytes([ACK]))

    def send_xmodem(self, data):
        """xm_send_packet / xm_send_eot."""
        block = 1
        for i in range(0, len(data), 128):
            frame = bytes([SOH, block, block ^ 0xFF]) + data[i:i + 128]
            crc = crc16_xmodem(data[i:i + 128])
            self.sock.sendall(frame + bytes([crc >> 8, crc & 0xFF]))
            if self.byte() != ACK:
                return False
            block = (block + 1) & 0xFF
        self.sock.sendall(bytes([EOT]))
        return self.byte() == ACK

    # ---- monitor commands
    def load(self, name):                         # L
        self.request("X", name)
        reply = self.byte()
        if reply != ACK:
            return reply, None, 0
        data, naks = self.recv_xmodem()
        if data is None:
            self.report(3)
            return "failed", None, naks
        try:
            back = Environment.from_stream(data)
            status = 0
        except EnvError:
            back, status = None, 7
        self.report(status)
        return ACK, back, naks

    def menu(self):                               # L without a name
        self.request("E", "")
        if self.byte() != ACK:
            return None
        data, _ = self.recv_xmodem()
        return [n for n in data.rstrip(b"\x1a").decode().split("\r\n") if n]

    def files(self):                              # F
        self.request("D", "")
        if self.byte() != ACK:
            return None
        data, _ = self.recv_xmodem()
        return data.rstrip(b"\x1a").decode()

    def write_disk(self, name):                   # W
        self.request("I", name)
        if self.byte() != ACK:
            return None
        count = bytes(self.byte() for _ in range(4))
        x = count[0] ^ count[1] ^ count[2] ^ count[3]
        assert self.byte() == x
        sectors = int.from_bytes(count, "big")
        data, _ = self.recv_xmodem()
        self.report(0 if data is not None and len(data) == sectors * 256 else 6)
        return data

    def upload(self, name, image, bad=(), dev=0):  # U
        self.request("P", name, (len(image) // 256).to_bytes(4, "big"))
        if self.byte() != ACK:
            return False
        if not self.send_xmodem(image):
            return False
        if not bad:
            return True
        self.request("B", name, bytes([dev]) + len(bad).to_bytes(2, "big"))
        if self.byte() != ACK:
            return False
        data = b"".join(n.to_bytes(2, "big") for n in bad)
        return self.send_xmodem(data.ljust(-(-len(data) // 128) * 128, b"\0"))


class Protocol(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def stored(self, name, size):
        """The server writes an upload after ACKing the EOT: wait for it."""
        path = os.path.join(self.dir, name)
        for _ in range(100):
            if os.path.exists(path) and os.path.getsize(path) == size:
                break
            time.sleep(0.02)
        with open(path, "rb") as f:
            return f.read()

    def put(self, name, data):
        with open(os.path.join(self.dir, name), "wb") as f:
            f.write(data)

    def start(self, fault=None, overwrite=False):
        a, b = socket.socketpair()
        b.settimeout(0.5)
        link = m20link.Link(b, "socket")
        store = m20server.Store(self.dir, overwrite)
        threading.Thread(target=self.serve_quiet,
                         args=(link, store, fault), daemon=True).start()
        self.addCleanup(b.close)
        self.addCleanup(a.close)                 # runs first: ends the server
        return FakeM20(a)

    @staticmethod
    def serve_quiet(link, store, fault):
        try:
            m20server.serve(link, store, fault, False)
        except (ConnectionError, OSError):
            pass

    # ---- environments
    def test_load_by_name(self):
        e = env([(parse_addr("6:4000"), os.urandom(1000))])
        self.put("Hello.m20x", e.stream())
        m20 = self.start()
        for name in ("hello", "HELLO.M20X"):
            reply, back, naks = m20.load(name)
            self.assertEqual(reply, ACK)
            self.assertEqual(back.regions, e.regions)
            self.assertEqual(naks, 0)

    def test_menu_and_unknown(self):
        for name in ("zeta", "Alpha", "disktool"):
            self.put(name + ".m20x", env([(parse_addr("6:0"), b"abc")]).stream())
        self.put("disk.img", bytes(256))
        m20 = self.start()
        self.assertEqual(m20.menu(), ["Alpha", "disktool", "zeta"])
        self.assertEqual(m20.load("")[0], CAN)          # the ROM never sends X ""
        self.assertEqual(m20.load("nosuch")[0], CAN)

    def test_menu_limit_and_empty(self):
        m20 = self.start()
        self.assertEqual(m20.menu(), [])
        stream = env([(parse_addr("6:0"), b"abc")]).stream()
        for i in range(40):
            self.put(f"e{i:02d}.m20x", stream)
        self.put("broken.m20x", b"not an environment")
        names = m20.menu()
        self.assertEqual(len(names), 35)
        self.assertNotIn("broken", names)

    def test_block_wrap(self):
        # 40000 bytes + manifest = 315 packets: block numbers pass 255 -> 0.
        e = env([(parse_addr("6:0"), os.urandom(40000))])
        self.put("big.m20x", e.stream())
        _, back, _ = self.start().load("big")
        self.assertEqual(back.regions, e.regions)

    def test_nak_recovery(self):
        e = env([(parse_addr("6:0"), os.urandom(600))])
        self.put("e.m20x", e.stream())
        _, back, naks = self.start(fault=(3, 2)).load("e")
        self.assertEqual(naks, 2)
        self.assertEqual(back.regions, e.regions)

    def test_too_many_errors(self):
        self.put("e.m20x", env([(parse_addr("6:0"), os.urandom(600))]).stream())
        _, back, _ = self.start(fault=(2, 20)).load("e")
        self.assertIsNone(back)

    # ---- listing
    def test_files(self):
        self.put("hello.m20x", env([(parse_addr("6:0"), b"abc")]).stream())
        self.put("pcos.img", bytes(1120 * 256))
        text = self.start().files()
        self.assertIn("hello", text)
        self.assertIn("pcos", text)
        self.assertIn("1120 sectors", text)
        self.assertTrue(all(len(line) <= 64 for line in text.split("\r\n")))

    # ---- disk images
    def test_write_disk(self):
        image = os.urandom(1120 * 256)
        self.put("disk.img", image)
        m20 = self.start()
        self.assertEqual(m20.write_disk("DISK"), image)
        self.assertIsNone(m20.write_disk("nosuch"))

    def test_write_disk_refuses_odd_size(self):
        self.put("odd.img", b"x" * 300)
        self.assertIsNone(self.start().write_disk("odd"))

    def test_upload(self):
        image = os.urandom(64 * 256)
        m20 = self.start()
        self.assertTrue(m20.upload("backup", image))
        self.assertEqual(self.stored("backup.img", len(image)), image)
        # exists now: refused without --overwrite
        self.assertFalse(m20.upload("backup", image))
        self.assertFalse(m20.upload("../evil", image))

    def test_upload_with_bad_sectors(self):
        image = os.urandom(32 * 256)
        m20 = self.start(overwrite=True)
        self.assertTrue(m20.upload("worn", image, bad=[3, 17, 300], dev=1))
        self.assertEqual(self.stored("worn.img", len(image)), image)
        path = os.path.join(self.dir, "worn.bad")
        for _ in range(100):
            if os.path.exists(path):
                break
            time.sleep(0.02)
        with open(path) as f:
            rows = [line.split() for line in f if not line.startswith("#")]
        self.assertEqual(rows, [["3", "0x0003"], ["17", "0x0011"], ["300", "0x012C"]])
        self.assertIn("3 bad", m20.files())
        # a clean upload of the same name removes the old list
        self.assertTrue(m20.upload("worn", image))
        for _ in range(100):
            if not os.path.exists(path):
                break
            time.sleep(0.02)
        self.assertFalse(os.path.exists(path))

    def test_bad_list_needs_image(self):
        m20 = self.start()
        m20.request("B", "nosuch", bytes([0]) + (1).to_bytes(2, "big"))
        self.assertEqual(m20.byte(), CAN)

    def test_upload_overwrite(self):
        self.put("backup.img", b"old")
        m20 = self.start(overwrite=True)
        image = os.urandom(16 * 256)
        self.assertTrue(m20.upload("backup", image))
        self.assertEqual(self.stored("backup.img", len(image)), image)


if __name__ == "__main__":
    unittest.main()
