"""Decodes a capture log written by mk2_proxy.py down to the MK2/Winmon level.

Usage:  python mk2_decode.py capture_XXXX.log [--trace]
  --trace  also prints every request/reply pair in chronological order

Based on the document "Interfacing with VE.Bus products - MK2 Protocol 3.14".
"""
import sys
from collections import defaultdict

# ---------------------------------------------------------------- names (PDF 7.3.13)
RAM_NAMES = {
    0: "UMainsRMS", 1: "IMainsRMS", 2: "UInverterRMS", 3: "IInverterRMS",
    4: "UBat", 5: "IBat", 6: "UBatRMS (ripple)", 7: "InverterPeriod",
    8: "MainsPeriod", 9: "SignedACLoadCurrent", 10: "VirtualSwitchPos",
    11: "IgnoreACInputState", 12: "MultiFuncRelayState", 13: "ChargeState(%)",
    14: "InverterPower(filt)", 15: "InverterPower(filt)b", 16: "OutputPower(filt)",
    17: "InverterPower", 18: "InverterPower b", 19: "OutputPower",
}
SETTING_NAMES = {
    0: "Flags0", 1: "Flags1", 2: "UBatAbsorption", 3: "UBatFloat", 4: "IBatBulk",
    5: "UInvSetpoint", 6: "IMainsLimit(AC1)", 7: "RepeatedAbsorptionTime",
    8: "RepeatedAbsorptionInterval", 9: "MaxAbsorptionDuration",
    10: "ChargeCharacteristic", 11: "UBatLowLimit(inverter)", 12: "UBatLowHysteresis",
    13: "NumberOfSlaves", 14: "ThreePhaseMode",
    15: "vsUsage", 16: "vsonIInvHigh", 17: "vsonUBatHigh", 18: "vsonUBatLow",
    19: "vstonIInvHigh", 20: "vstonUBatHigh", 21: "vstonUBatLow", 22: "vstonNotCharging",
    23: "vstonFanOn", 24: "vstonTemperatureAlarm", 25: "vstonLowBatteryAlarm",
    26: "vstonOverloadAlarm", 27: "vstonUBatRippleAlarm", 28: "vsoffIInvLow",
    29: "vsoffUBatHigh", 30: "vsoffUBatLow", 31: "vstoffIInvLow", 32: "vstoffUBatHigh",
    33: "vstoffUBatLow", 34: "vstoffCharging", 35: "vstoffFanOff",
    36: "vstoffChargeBulkFinished", 37: "vstoffNoVSOnCondition", 38: "vstoffNoACInput",
    39: "vstoffTemperatureAlarm", 40: "vstoffLowBatteryAlarm", 41: "vstoffOverloadAlarm",
    42: "vstoffUBatRippleAlarm", 43: "vsMinimumOnTime",
    44: "UMainsLowAccept", 45: "UMainsLowHyst", 46: "UMainsHighAccept", 47: "UMainsHighHyst",
    48: "AssistCurrentBoost", 49: "IMainsLimit(AC2)", 50: "AESLowCurrent", 51: "AESHyst",
    52: "vs2onILoadHigh", 53: "vs2onILoadHigh t", 54: "vs2onUBatLow", 55: "vs2onUBatLow t",
    56: "vs2offILoadLow", 57: "vs2offILoadLow t", 58: "vs2offUBatHigh", 59: "vs2offUBatHigh t",
    60: "Flags2", 61: "Flags3", 62: "vsInverterPeriodTime", 63: "UBatLowPreAlarmOffset",
    64: "BatteryCapacity", 65: "ChargeEfficiency/BulkToAbs%",
}
FLAG_NAMES = {
    0: "MultiPhaseSystem", 1: "MultiPhaseLeader", 2: "60Hz", 3: "DisableWaveCheck",
    4: "DoNotStopAfter10HrBulk", 5: "AssistEnabled", 6: "DisableCharge",
    7: "(inverse of bit3)", 8: "DisableAES", 9: "NotPromoted!", 10: "NotPromoted!",
    11: "EnableReducedFloat", 12: "NotPromoted!", 13: "DisableGroundRelay",
    14: "WeakACInput", 15: "RemoteOverrulesAC2",
    16: "vsonBulkProtection", 17: "vsonTempPreAlarm", 18: "vsonLowBatPreAlarm",
    19: "vsonOverloadPreAlarm", 20: "vsonRipplePreAlarm", 21: "vsoffTempPreAlarm",
    22: "vsoffLowBatPreAlarm", 23: "vsoffOverloadPreAlarm", 24: "vsoffRipplePreAlarm",
    25: "vsonWhenGeneralFailure", 26: "vsInvert", 27: "AcceptWideInputFreq",
    28: "DynamicCurrentLimiter", 29: "TubularPlateCurve", 30: "RemoteOverrulesAC1",
    31: "LowPowerShutdownInAES",
    32: "vs2offWhenAC1Available", 33: "vs2Invert", 34: "vsSetInverterPeriodTime",
}
WINMON_CMDS = {
    0x05: "SoftwareVersionPart0", 0x06: "SoftwareVersionPart1", 0x0E: "GetSetDeviceState",
    0x30: "ReadRAMVar", 0x31: "ReadSetting", 0x32: "WriteRAMVar", 0x33: "WriteSetting",
    0x34: "WriteData", 0x35: "GetSettingInfo", 0x36: "GetRAMVarInfo",
    0x37: "WriteViaID", 0x38: "ReadSnapShot",
}
WRITE_CMDS = {0x32, 0x33, 0x34, 0x37}
WINMON_LETTERS = {0x57: "W", 0x58: "X", 0x59: "Y", 0x5A: "Z"}
MAX_LEN = 0x20  # the longest observed MK2 frame is ~16 bytes; a larger length byte = garbage
# "Reset Multi" frame of the official configuration software (observed 2x on 29.10.2026 with Send settings + reset Yes).
# Not MK2 format: no 0xFF byte and no MK2 checksum. The meaning of each byte is unknown.
RESET_FRAME = bytes.fromhex("06 21 00 00 00 00 03 C4")


def s16(v):
    return v - 0x10000 if v & 0x8000 else v


def setting_scale(sc):
    sc = s16(sc)
    if sc == 0:
        return None
    return float(sc) if sc > 0 else 1.0 / (-sc)


def ram_scale(sc):
    """Returns (scale, signed) or None if not supported."""
    scs = s16(sc)
    if scs == 0:
        return None
    a = abs(scs)
    scale = 1.0 / (0x8000 - a) if a >= 0x4000 else float(a)
    return scale, scs < 0


def fmt(x):
    if not isinstance(x, float):
        return str(x)
    if x != 0 and abs(x) < 0.01:
        return f"{x:.4g}"  # small scales (e.g. 1/1961) with significant digits
    return f"{x:.3f}".rstrip("0").rstrip(".")


def period_to_hz(display):
    """RAM 7/8: PDF 'time-base 0.1s' -> period in s = display * 0.1."""
    return 1.0 / (display * 0.1) if display > 0 else None


# ---------------------------------------------------------------- reading the log
def read_log(path):
    """Returns a list of (time, direction, bytes)."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.split()
            if len(p) < 3 or p[1] not in ("TX", "RX"):
                continue
            out.append((float(p[0]), p[1], bytes(int(h, 16) for h in p[2:])))
    return out


def frames(chunks):
    """Assembles MK2 frames from the byte stream of one direction; resynchronises on a checksum error."""
    buf = bytearray()
    times = []
    res = []
    for t, data in chunks:
        for b in data:
            buf.append(b)
            times.append(t)
        while buf:
            if buf[:len(RESET_FRAME)] == RESET_FRAME:
                res.append((times[0], RESET_FRAME))
                del buf[:len(RESET_FRAME)]
                del times[:len(RESET_FRAME)]
                continue
            if (buf[0] & 0x7F) > MAX_LEN:  # cannot be a length byte -> garbage
                del buf[:1]
                del times[:1]
                continue
            n = (buf[0] & 0x7F) + 2
            if len(buf) < n:
                break
            fr = bytes(buf[:n])
            if sum(fr) & 0xFF == 0 and n >= 3:
                res.append((times[0], fr))
                del buf[:n]
                del times[:n]
            else:  # garbage -> drop a byte
                del buf[:1]
                del times[:1]
    return res


# ---------------------------------------------------------------- interpretation
class State:
    def __init__(self):
        self.setting_info = {}          # id -> dict
        self.setting_val = {}           # id -> raw
        self.ram_info = {}              # id -> (sc, offset)
        self.ram_vals = defaultdict(list)  # id -> [(t, raw)]
        self.version = {}
        self.unknown = defaultdict(list)  # cmd -> [(t, txhex, rxhex)]
        self.mk2 = []                   # other MK2-level frames
        self.writes = []
        self.write_target = None        # (0x32|0x33, id) waiting for 0x34
        self.setting_writes = []        # (t, id, old raw | None, new raw)
        self.ram_writes = []            # (t, id, raw)


def decode(path, trace=False):
    log = read_log(path)
    tx = frames([(t, d) for t, dr, d in log if dr == "TX"])
    rx = frames([(t, d) for t, dr, d in log if dr == "RX"])
    events = sorted([(t, "TX", f) for t, f in tx] + [(t, "RX", f) for t, f in rx], key=lambda e: e[0])

    st = State()
    pending = {}  # letter -> (t, cmd, args)
    for t, d, fr in events:
        if fr == RESET_FRAME:
            st.writes.append((t, "RESET MULTI " + fr.hex(" ")))
            if trace:
                print(f"{t:9.3f} {d} *** RESET MULTI *** {fr.hex(' ')}")
            continue
        if fr[1] != 0xFF:          # VE.Bus frame (e.g. F reply), not MK2
            st.mk2.append((t, d, fr))
            continue
        cmd = fr[2]
        payload = fr[3:-1]
        if cmd in WINMON_LETTERS:
            L = WINMON_LETTERS[cmd]
            if d == "TX":
                wc = payload[0]
                args = payload[1:]
                pending[L] = (t, wc, args)
                if wc in WRITE_CMDS:
                    st.writes.append((t, fr.hex(" ")))
                if wc in (0x32, 0x33):          # WriteRAMVar / WriteSetting: remember the target
                    st.write_target = (wc, int.from_bytes(args[:2], "little"))
                elif wc == 0x34 and st.write_target:  # WriteData: value to the target
                    kind, wid = st.write_target
                    raw = int.from_bytes(args[:2], "little")
                    if kind == 0x33:
                        st.setting_writes.append((t, wid, st.setting_val.get(wid), raw))
                        st.setting_val[wid] = raw
                    else:
                        st.ram_writes.append((t, wid, raw))
                    st.write_target = None
                if trace:
                    print(f"{t:9.3f} {L}> {WINMON_CMDS.get(wc, f'?0x{wc:02X}')} {args.hex(' ')}")
                if wc not in WINMON_CMDS:
                    st.unknown[wc].append((t, args.hex(" "), None))
            else:
                req = pending.get(L)
                for i in range(0, len(payload) - 2, 3):
                    handle_reply(st, t, L, req, payload[i], payload[i + 1] | payload[i + 2] << 8, trace)
        else:
            st.mk2.append((t, d, fr))
            if trace:
                print(f"{t:9.3f} {d} MK2 '{chr(cmd) if 32 <= cmd < 127 else cmd}' {payload.hex(' ')}")
    return st


def handle_reply(st, t, L, req, rc, val, trace):
    if trace:
        print(f"{t:9.3f} {L}< 0x{rc:02X} {val:04X} ({val})")
    if req is None:
        return
    _, wc, args = req
    rid = int.from_bytes(args[:2], "little") if args else None
    if wc == 0x35 and rid is not None:
        info = st.setting_info.setdefault(rid, {})
        key = {0x89: "sc", 0x8A: "offset", 0x8B: "default", 0x8C: "min", 0x8D: "max"}.get(rc)
        if key:
            info[key] = val
    elif wc == 0x36 and rid is not None:
        if rc == 0x8E:
            st.ram_info[rid] = [val, st.ram_info.get(rid, [0, 0])[1]]
        elif rc == 0x8F:
            st.ram_info.setdefault(rid, [0, 0])[1] = val
    elif wc == 0x31 and rid is not None:
        if rc == 0x86:
            st.setting_val[rid] = val
        elif rc == 0x91:
            st.setting_val[rid] = None
    elif wc == 0x30 and args:
        if rc == 0x85:
            st.ram_vals[args[0]].append((t, val))
    elif wc in (0x05, 0x06):
        st.version[rc] = val
    elif wc not in WINMON_CMDS:
        st.unknown[wc].append((t, args.hex(" "), f"{rc:02X} {val & 0xFF:02X} {val >> 8:02X}"))


# ---------------------------------------------------------------- report
def report(st):
    if 0x82 in st.version and 0x83 in st.version:
        v = st.version[0x82] | st.version[0x83] << 16
        print(f"Device firmware: {v}  (0x{v:08X})\n")

    print("=== SETTINGS (0x35 info + 0x31 value) ===")
    print(f"{'ID':>3} {'name':28} {'raw':>6} {'value':>9} {'default':>9} {'min':>8} {'max':>8}  scale  offs")
    for sid in sorted(set(st.setting_info) | set(st.setting_val)):
        info = st.setting_info.get(sid, {})
        sc = info.get("sc")
        scale = setting_scale(sc) if sc is not None else None
        off = s16(info.get("offset", 0))
        name = SETTING_NAMES.get(sid, f"setting_{sid}")
        raw = st.setting_val.get(sid)
        if sc is not None and scale is None:
            print(f"{sid:>3} {name:28} (not supported)")
            continue

        def disp(x):
            if x is None or scale is None:
                return "-"
            return fmt(scale * (x + off))
        print(f"{sid:>3} {name:28} {raw if raw is not None else '-':>6} {disp(raw):>9} "
              f"{disp(info.get('default')):>9} {disp(info.get('min')):>8} {disp(info.get('max')):>8}  "
              f"{fmt(scale) if scale else '-':5} {off}")

    # expand the flags
    for fid, base in ((0, 0), (1, 16), (60, 32), (61, 48)):
        raw = st.setting_val.get(fid)
        mask = st.setting_info.get(fid, {}).get("max")
        if raw is None:
            continue
        print(f"\n--- {SETTING_NAMES[fid]} = 0x{raw:04X} (supported bits mask 0x{mask or 0:04X}) ---")
        for b in range(16):
            if mask is not None and not (mask >> b) & 1:
                continue
            on = (raw >> b) & 1
            print(f"   flag[{base + b:2}] {'X' if on else '.'} {FLAG_NAMES.get(base + b, '?')}")

    print("\n=== RAM VARIABLES (0x36 info + 0x30 readings) ===")
    print(f"{'ID':>3} {'name':22} {'n':>4} {'latest':>10} {'min':>9} {'max':>9}  scale  offs")
    for rid in sorted(set(st.ram_info) | set(st.ram_vals)):
        sc, off = st.ram_info.get(rid, [None, 0])
        name = RAM_NAMES.get(rid, f"ram_{rid}")
        vals = st.ram_vals.get(rid, [])
        if sc is not None and ram_scale(sc) is None:
            if vals:
                print(f"{rid:>3} {name:22} (info: not supported, but read {len(vals)}x)")
            continue
        if off == 0x8000:  # bit variable
            bit = s16(sc) - 1
            last = vals[-1][1] if vals else None
            print(f"{rid:>3} {name:22} {len(vals):>4} {'-' if last is None else (last >> bit) & 1:>10}   (bit {bit})")
            continue
        scale, signed = ram_scale(sc) if sc is not None else (1.0, False)
        offs = s16(off)

        def conv(x):
            x = s16(x) if signed else x
            return scale * (x + offs)
        conv_vals = [conv(v) for _, v in vals]
        if conv_vals:
            extra = ""
            if rid in (7, 8):
                hz = [period_to_hz(v) for v in conv_vals]
                extra = f"  = {hz[-1]:.2f} Hz ({min(hz):.2f}..{max(hz):.2f})"
            print(f"{rid:>3} {name:22} {len(vals):>4} {fmt(conv_vals[-1]):>10} {fmt(min(conv_vals)):>9} "
                  f"{fmt(max(conv_vals)):>9}  {fmt(scale):5} {offs}{' signed' if signed else ''}{extra}")
        else:
            print(f"{rid:>3} {name:22} {0:>4} {'-':>10}  {'':>9} {'':>9}  {fmt(scale):5} {offs}{' signed' if signed else ''}")

    if st.unknown:
        print("\n=== UNDOCUMENTED WINMON COMMANDS ===")
        for wc, lst in sorted(st.unknown.items()):
            replies = sorted({r for _, _, r in lst if r})
            argsets = sorted({a for _, a, _ in lst})
            print(f"0x{wc:02X}: {len(lst)} events, args {argsets}, replies {replies}")

    print("\n=== OTHER MK2 FRAMES (unique) ===")
    seen = defaultdict(int)
    for _, d, fr in st.mk2:
        seen[(d, fr.hex(' '))] += 1
    for (d, h), n in sorted(seen.items()):
        print(f"{d} x{n:<4} {h}")

    print(f"\nWrite commands observed: {len(st.writes)}")
    for t, h in st.writes:
        print(f"  {t:9.3f} {h}")

    if st.setting_writes:
        print("\n=== SETTING WRITES (0x33 + 0x34) ===")
        for t, sid, old, new in st.setting_writes:
            info = st.setting_info.get(sid, {})
            scale = setting_scale(info["sc"]) if info.get("sc") else None
            off = s16(info.get("offset", 0))

            def sv(x):
                return "?" if x is None else (fmt(scale * (x + off)) if scale else str(x))
            name = SETTING_NAMES.get(sid, f"setting_{sid}")
            print(f"  {t:9.3f}  ID {sid:3} {name:28} {sv(old):>8} -> {sv(new):<8} (raw {old} -> {new})")
            base = {0: 0, 1: 16, 60: 32, 61: 48}.get(sid)
            if base is not None and old is not None:
                for b in range(16):
                    if (old ^ new) >> b & 1:
                        print(f"             flag[{base + b:2}] {FLAG_NAMES.get(base + b, '?'):26} "
                              f"{'ON' if old >> b & 1 else 'off'} -> {'ON' if new >> b & 1 else 'off'}")
    for t, rid, raw in st.ram_writes:
        print(f"  {t:9.3f}  RAM write ID {rid} {RAM_NAMES.get(rid, '?')} = {raw}")


if __name__ == "__main__":
    path = sys.argv[1]
    st = decode(path, trace="--trace" in sys.argv)
    report(st)
