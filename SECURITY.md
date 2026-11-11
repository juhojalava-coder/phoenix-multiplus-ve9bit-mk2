# Security and safety policy

This project talks to a real inverter/charger that is connected to mains and batteries. Please read the
[Safety](README.md#safety) section of the README before using it.

## Scope

Please report anything that could make the program change a device without the user's intent, for example:

- a command reaching the device that is not in the whitelist of `mk2_link.py`
- the knob or switch being sent in monitor only mode, or on unverified firmware
- a settings write that continues after a failed write, or writes outside the validated limits
- control from Venus/VRM working although it is not enabled
- passwords or other secrets ending up in logs, release packages or published data

## Reporting

Use GitHub's **private vulnerability reporting** (Security tab > Report a vulnerability) if it is enabled for this
repository, otherwise open an issue **without** exploit details and ask for a private contact. Include the program
version (`VERSION`), the device firmware and what you observed (a `capture_direct_*.log` helps; remove anything
private from it first).

This is a hobby project maintained in spare time. There is no guaranteed response time and no warranty
(see [LICENSE](LICENSE)).

## Deployment notes

- MQTT to the Venus is unencrypted (port 1883). Use the program only on a trusted, closed local network and never
  expose the Venus MQTT broker to the internet.
- If the Venus is connected to VRM with two-way communication, any remote control you allow also works over the
  internet.
- A remembered MQTT password is stored in plain text in `mk2_panel_state.json`. Keep the file private, or use the
  `MK2_MQTT_PASSWORD` environment variable instead.
