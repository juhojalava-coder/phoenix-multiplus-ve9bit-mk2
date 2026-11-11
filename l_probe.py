"""Experiment: MK2 'L' (LED status, PDF chapter 4) vs. the Winmon 0x02 -> 0x81 LED word.

Usage: python l_probe.py COM1 [rounds=5]
Does not send switch, knob or settings commands (only V, R, S NO-SEND, version, L, 0x02).
Goal: if 'L' gives the LED byte according to the PDF, the alarm bits (overload/low batt/temp) come from the PDF.
"""
import sys
import time

import mk2_link

PDF_L = ["mains", "absorption", "bulk", "float", "inverter", "overload", "low battery", "temperature"]
W81 = {4: "inverter", 5: "mains", 6: "bulk", 7: "absorption", 8: "float"}


def names(byte, table):
    return [table[b] for b in range(8) if byte >> b & 1]


port = sys.argv[1] if len(sys.argv) > 1 else "COM1"
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 5
link = mk2_link.MK2(port)
print("loki:", link.logf.name)
link.mk2_cmd(b"V", 0x56)
link.mk2_cmd(b"R", 0x56, timeout=1.0)
time.sleep(0.3)
print("S-kuittaus:", link.mk2_cmd(bytes.fromhex("53 04 00 ff 01 10 00 04"), 0x53) is not None)

for i in range(rounds):
    fr = link.mk2_cmd(b"L", 0x4C, timeout=0.8)
    if fr is None:
        print(f"{i}: L -> no reply")
    else:
        data = fr[3:-1]
        on = data[0] if data else None
        blink = data[1] if len(data) > 1 else None
        print(f"{i}: L -> {fr.hex(' ')}   data={data.hex(' ')}")
        if on is not None:
            print(f"     PDF-tulkinta: on={on:08b} {names(on, PDF_L)}  blink={blink:08b} {names(blink, PDF_L) if blink is not None else ''}")
    res = link.winmon(0x02)
    for rc, val in res:
        if rc == 0x81:
            word = val
            print(f"     0x02 -> 0x81 = 0x{word:04X}  {[W81.get(b, f'?b{b}') for b in range(15) if word >> b & 1]}")
    time.sleep(1.0)
link.close()
print("done")
