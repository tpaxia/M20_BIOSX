# Copyright (C) 2026 Salvatore Paxia
# SPDX-License-Identifier: GPL-3.0-or-later
"""m20link.py — serial transport for the M20 BIOS 2.0x server.

A byte pipe (real serial port via pyserial, or an accepted TCP socket for a
MAME bitbanger) and the XMODEM-CRC sender/receiver: 128-byte packets,
CRC-16/XMODEM, NAK retransmission, ten retries, block numbers wrapping
through 0.

Taken unchanged from PCOS/netutils/nserver.py (M20 repository), which follows
the Zilog S8000 serial installer, so that this server has no other
dependency.
"""

import socket
import sys
import time

SOH, EOT, ACK, NAK, CAN, PAD = 0x01, 0x04, 0x06, 0x15, 0x18, 0x1A

PACKET_SIZE = 128
MAX_RETRIES = 10


# ---------------------------------------------------------------- transport

class Link:
    """A byte pipe: a real serial port or an accepted socket."""

    def __init__(self, stream, kind):
        self._stream = stream
        self.kind = kind

    @classmethod
    def open_serial(cls, name, baud):
        try:
            import serial
        except ImportError:
            sys.exit("pyserial is required for serial ports: pip install pyserial")
        port = serial.Serial(name, baudrate=baud, bytesize=8, parity="N",
                             stopbits=1, timeout=0.5)
        while port.in_waiting:
            port.read(port.in_waiting)
        return cls(port, "serial")

    @classmethod
    def listen(cls, spec):
        host, _, port = spec.rpartition(":")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host or "127.0.0.1", int(port)))
        listener.listen(1)
        print(f"waiting for a connection on {host or '127.0.0.1'}:{port}", flush=True)
        conn, peer = listener.accept()
        listener.close()
        print(f"connected from {peer[0]}:{peer[1]}", flush=True)
        conn.settimeout(0.5)
        return cls(conn, "socket")

    def read_byte(self, timeout):
        """One byte, or None once `timeout` seconds have passed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.kind == "serial":
                    data = self._stream.read(1)
                else:
                    data = self._stream.recv(1)
                    if data == b"":
                        raise ConnectionResetError("peer closed the connection")
            except socket.timeout:
                continue
            if data:
                return data[0]
        return None

    def write(self, data, pace=0.0):
        if pace:
            for byte in data:
                self._write_all(bytes([byte]))
                time.sleep(pace)
        else:
            self._write_all(data)

    def _write_all(self, data):
        if self.kind == "serial":
            self._stream.write(data)
            self._stream.flush()
        else:
            self._stream.sendall(data)

    def close(self):
        self._stream.close()


# ------------------------------------------------------------------- xmodem

def crc16_xmodem(payload):
    crc = 0
    for byte in payload:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def xmodem_packet(block, payload):
    assert len(payload) == PACKET_SIZE
    crc = crc16_xmodem(payload)
    return bytes([SOH, block & 0xFF, (~block) & 0xFF]) + payload + \
        bytes([crc >> 8, crc & 0xFF])


def send_stream(link, payload, pace=0.0, verbose=False):
    """Send text or bytes as XMODEM-CRC packets. True when the M20 ACKed EOT."""
    data = payload.encode("ascii", errors="replace") if isinstance(payload, str) \
        else payload
    packets = [data[i:i + PACKET_SIZE] for i in range(0, len(data), PACKET_SIZE)] or [b""]
    packets[-1] = packets[-1].ljust(PACKET_SIZE, bytes([PAD]))

    block = 1
    for payload in packets:
        frame = xmodem_packet(block, payload)
        for attempt in range(MAX_RETRIES):
            link.write(frame, pace)
            reply = link.read_byte(timeout=10.0)
            if reply == ACK:
                break
            if reply == CAN:
                if verbose:
                    print("  receiver cancelled", flush=True)
                return False
            if verbose:
                name = {NAK: "NAK", None: "timeout"}.get(reply, f"0x{reply:02x}"
                                                         if reply is not None else "timeout")
                print(f"  block {block}: {name}, retry {attempt + 1}", flush=True)
        else:
            print(f"  block {block}: giving up after {MAX_RETRIES} retries", flush=True)
            return False
        block = (block + 1) & 0xFF

    for _ in range(MAX_RETRIES):
        link.write(bytes([EOT]))
        if link.read_byte(timeout=5.0) == ACK:
            return True
    return False


def recv_stream(link, expected, verbose=False):
    """Receive XMODEM-CRC packets until EOT. Returns bytes, or None on failure."""
    link.write(bytes([ACK]))                     # the M20 starts on our ACK
    data = bytearray()
    block = 1
    errors = 0
    while True:
        first = link.read_byte(timeout=10.0)
        if first is None or first == CAN:
            return None
        if first == EOT:
            link.write(bytes([ACK]))
            return bytes(data[:expected])
        if first != SOH:
            errors += 1
            if errors > MAX_RETRIES:
                return None
            link.write(bytes([NAK]))
            continue

        header = [link.read_byte(timeout=2.0) for _ in range(2)]
        payload = bytearray()
        for _ in range(PACKET_SIZE + 2):
            byte = link.read_byte(timeout=2.0)
            if byte is None:
                break
            payload.append(byte)

        good = (None not in header and len(payload) == PACKET_SIZE + 2 and
                header[0] ^ header[1] == 0xFF and
                crc16_xmodem(payload[:PACKET_SIZE]) ==
                (payload[PACKET_SIZE] << 8 | payload[PACKET_SIZE + 1]))
        if not good:
            errors += 1
            if verbose:
                print(f"  packet {block}: damaged, NAK", flush=True)
            if errors > MAX_RETRIES:
                return None
            link.write(bytes([NAK]))
            continue

        if header[0] == block:
            data.extend(payload[:PACKET_SIZE])
            block = (block + 1) & 0xFF
        elif verbose:
            print(f"  packet {header[0]}: duplicate, ACK", flush=True)
        errors = 0
        link.write(bytes([ACK]))
