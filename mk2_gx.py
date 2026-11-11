"""MK2 -> Venus bridge without a UI (to run as a service: Venus GX daemontools, Kubuntu systemd).

Same as the panel's direct mode + --mqtt, but without tkinter:
  1. reads the settings (mk2.toml or sectioned .json, see mk2_config) and opens the MK2 with the configured limits
  2. monitor only by default ([control] enabled = false): nothing is sent to the device. With control on, the knob
     and switch are restored from the state file like the panel (mk2_state.startup_knob/startup_switch); if no
     switch is stored, none is sent and the device keeps its state. Unverified firmware: read-only.
  3. starts the bridge (mk2_mqtt.start_background). Current limit and Mode control coming from the Venus
     (if allowed in the settings) are saved to the state file
  4. SIGTERM/SIGINT -> connected:0 to the Venus, port closed, exit 0
  5. watchdog: if the MK2 link breaks (no fresh data for WATCHDOG_S seconds, or a connection error),
     the program exits with code 1 so that the service manager (daemontools/systemd) restarts it

Usage:
  python3 mk2_gx.py --config /data/mk2gx/mk2gx.json
  python mk2_gx.py --config mk2.toml --port COM10 --state my_state.json
Logs go to stdout (daemontools: multilog). The capture log (all MK2 traffic) is written only with --capture-dir.
"""
import argparse
import logging
import signal
import sys
import threading
import time

import mk2_config
import mk2_mqtt
import mk2_state

log = logging.getLogger("mk2_gx")

WATCHDOG_S = 60.0          # no fresh measurement within this many seconds -> exit (restart)
STARTUP_GRACE_S = 60.0     # opening the connection and the first measurements


def state_path(app, override=None):
    if override:
        return override
    if app.state.path:
        return app.state.path
    return mk2_state.PANEL_STATE_FILE


def link_alive(link, now, started, watchdog_s=WATCHDOG_S, grace_s=STARTUP_GRACE_S):
    """(ok, reason). The link is alive if there is no error and some measurement is fresh (or startup is in progress)."""
    if getattr(link, "error", None):
        return False, f"MK2 error: {link.error}"
    if now - started < grace_s:
        return True, ""
    ts = [ent[2] for ent in getattr(link, "values", {}).values() if ent and ent[1] is not None]
    if not ts or now - max(ts) > watchdog_s:
        return False, f"no fresh MK2 data for {watchdog_s:g} s"
    return True, ""


def main(argv=None):
    ap = argparse.ArgumentParser(description="MK2 -> Venus GX bridge without a UI")
    ap.add_argument("--config", default=None, help="mk2.toml or sectioned .json (default MK2_CONFIG / mk2.toml)")
    ap.add_argument("--port", default=None, help="overrides [serial] port from the settings")
    ap.add_argument("--state", default=None, help="state file (overrides [state] path from the settings)")
    ap.add_argument("--capture-dir", default=None, help="write MK2 traffic to a capture_direct_*.log file")
    ap.add_argument("--allow-unknown-firmware", action="store_true",
                    help="RISKY: allow control on device firmware that has not been verified (default: read-only)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        app = mk2_config.load(args.config)       # settings error -> exit before opening the port
        cfg = app.mqtt_config()
    except (mk2_config.ConfigError, ValueError) as e:
        log.error("settings error: %s", e)
        return 2
    import mk2_link
    port = args.port or app.serial.port
    spath = state_path(app, args.state)
    saved = mk2_state.load_panel_state(spath)
    log.info("settings %s, port %s, state %s %s", app.path or "(defaults)", port, spath, saved)
    log.info("control from the Venus: current limit %s, Mode %s", cfg.allow_current_limit_control, cfg.allow_mode_control)

    stop_ev = threading.Event()

    def on_signal(signum, _frame):
        log.info("signal %s: stopping", signum)
        stop_ev.set()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    try:
        link = mk2_link.MK2(port, log_dir=args.capture_dir, limits=app.link_limits(),
                            control=app.control.enabled, allow_unknown_firmware=args.allow_unknown_firmware)
    except Exception as e:  # noqa: BLE001 - port missing etc. -> the service manager retries
        log.error("opening the MK2 port failed (%s): %s", port, e)
        time.sleep(5)       # avoid a fast restart loop
        return 1
    if app.control.enabled:
        link.desired_knob = mk2_state.startup_knob(saved, link.knob_min, link.knob_max)
        link.desired_switch = mk2_state.startup_switch(saved)
        log.info("control on, startup: knob %.1f, switch %s (sent only on verified firmware)", link.desired_knob / 10,
                 mk2_link.SWITCH_STATES.get(link.desired_switch, "not sent (device keeps its state)"))
    else:
        log.info("monitor only ([control] enabled = false): no knob or switch command is sent")
    link.start()

    def save(updates):
        try:
            mk2_state.save_panel_state(updates, spath)
        except OSError as e:
            log.warning("saving the state file failed: %s", e)

    stop_bridge = mk2_mqtt.start_background(link, cfg, on_knob=lambda raw: save({"knob": raw}),
                                            on_switch=lambda s: save({"switch": s}))
    rc, started = 0, time.time()
    try:
        while not stop_ev.wait(5.0):
            ok, why = link_alive(link, time.time(), started)
            if not ok:
                log.error("%s: stopping (restart by the service manager)", why)
                rc = 1
                break
    finally:
        stop_bridge()           # first connected:0 to the Venus, then close the port
        link.close()
        log.info("stopped (code %d)", rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
