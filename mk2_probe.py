"""MK2 probe: read commands only. Usage: python mk2_probe.py COM1 V L F0 A0
Commands: a letter + optional hex bytes, e.g. F0 = 'F' 0x00, A0 = 'A' 0x00 (read address).
"""
import sys, time, serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM1"
CMDS = sys.argv[2:] or ["V"]
BAUD = 2400  # MK2: 2400 8N1, DTR high (pyserial sets it by default)

# Safety: only read commands are allowed
SAFE = {"V", "L", "F", "A"}


def frame(cmd: bytes) -> bytes:
    """[len][0xFF][cmd...][checksum]; the sum of all bytes = 0 (mod 256)"""
    body = bytes([len(cmd) + 1, 0xFF]) + cmd
    return body + bytes([(-sum(body)) & 0xFF])


def hexs(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def split_frames(buf: bytes):
    """Split the received data into frames and check the checksum."""
    i = 0
    while i < len(buf):
        n = (buf[i] & 0x7F) + 2  # len byte + data + checksum
        f = buf[i:i + n]
        ok = len(f) == n and sum(f) & 0xFF == 0
        yield f, ok
        i += n


def parse_cmd(s: str) -> bytes:
    c = s[0].upper()
    if c not in SAFE:
        raise SystemExit(f"Command {c} is not in the list of allowed read commands")
    if c == "A":  # 'A' <action=0 read> <address>
        return b"A" + bytes([0, 0])
    rest = bytes.fromhex(s[1:].rjust(2, "0")) if len(s) > 1 else b""
    return c.encode() + rest


with serial.Serial(PORT, BAUD, bytesize=8, parity="N", stopbits=1, timeout=0.5) as s:
    s.reset_input_buffer()
    for c in CMDS:
        tx = frame(parse_cmd(c))
        s.reset_input_buffer()
        s.write(tx)
        print(f"TX {c:4}: {hexs(tx)}")
        time.sleep(0.8)
        rx = s.read(512)
        if not rx:
            print("  RX: (no reply)")
        for f, ok in split_frames(rx):
            tag = chr(f[2]) if len(f) > 2 and f[1] == 0xFF and 32 <= f[2] < 127 else f"0x{f[1]:02X}"
            print(f"  RX [{tag}] {'OK ' if ok else 'BAD'}: {hexs(f)}")
