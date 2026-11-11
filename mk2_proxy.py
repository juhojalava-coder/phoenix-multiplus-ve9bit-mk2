"""MK2 serial traffic relay + logger.

configuration program  <->  VIRT (e.g. COM20)  ==com0com==  VIRT_PEER (e.g. COM21)  <->  [this script]  <->  REAL (COM1, MK2)

Usage:  python mk2_proxy.py COM21 COM1 [baud]
Select COM20 (the other end of the pair) as the port in the configuration program.

Logging: capture_YYYYmmdd_HHMMSS.log, lines:  time  direction  hex   (PC->MK2 = "TX", MK2->PC = "RX")
Data is grouped into frames by a silent gap (GAP).
"""
import sys, time, threading, datetime, serial

PEER = sys.argv[1]
REAL = sys.argv[2]
BAUD = int(sys.argv[3]) if len(sys.argv) > 3 else 2400
GAP = 0.03  # s; a longer pause ends the group

logname = datetime.datetime.now().strftime("capture_%Y%m%d_%H%M%S.log")
logf = open(logname, "a", buffering=1, encoding="utf-8")
lock = threading.Lock()
t0 = time.perf_counter()
stop = threading.Event()


def log(direction: str, data: bytes):
    line = f"{time.perf_counter() - t0:10.3f}  {direction}  " + " ".join(f"{b:02X}" for b in data)
    with lock:
        print(line)
        logf.write(line + "\n")


def pump(src: serial.Serial, dst: serial.Serial, direction: str):
    buf = bytearray()
    last = time.perf_counter()
    while not stop.is_set():
        try:
            chunk = src.read(src.in_waiting or 1)
        except serial.SerialException as e:
            print("error", direction, e)
            stop.set()
            return
        now = time.perf_counter()
        if chunk:
            dst.write(chunk)  # relay at once, logging does not slow it down
            if buf and now - last > GAP:
                log(direction, bytes(buf))
                buf.clear()
            buf += chunk
            last = now
        elif buf and now - last > GAP:
            log(direction, bytes(buf))
            buf.clear()


def mk(port):
    return serial.Serial(port, BAUD, bytesize=8, parity="N", stopbits=1, timeout=0.01)


with mk(PEER) as a, mk(REAL) as b:
    print(f"Relay {PEER} <-> {REAL} @ {BAUD}, log: {logname}. Ctrl+C stops.")
    ts = [
        threading.Thread(target=pump, args=(a, b, "TX"), daemon=True),
        threading.Thread(target=pump, args=(b, a, "RX"), daemon=True),
    ]
    for t in ts:
        t.start()
    try:
        while not stop.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        stop.set()
logf.close()
