"""The dialogs that stand between "plugged in" and "capturing".

Three things go wrong when somebody connects real hardware for the first time,
and each used to end in an error message that told them to open a terminal:

- the SocketCAN device exists but is down, because the kernel, not the
  application, owns its bitrate;
- there is no CAN device at all, and they wanted to try the application
  without hardware;
- the adapter is a USB device that only root can open, because no udev rule
  grants the user access to it.

All three are fixed by running one command as root. This module asks for that
the way a desktop application should, through PolicyKit or whatever the
session provides, and says plainly what it is about to run before running it.

The application itself never runs as root and never sees a password.
"""
from __future__ import annotations

import os

from PyQt6.QtWidgets import QCheckBox, QMessageBox

from canlab.core import privileged as pv


def interactive() -> bool:
    """Whether there is a person here to answer a dialog.

    Under Qt's offscreen platform there is not: that is the test suite, the
    demo recorders and any scripted run. A modal dialog there has nobody to
    dismiss it, and its nested event loop repaints widgets that the caller is
    in the middle of tearing down, which crashes rather than hangs. These
    prompts are a desktop safeguard, so they are skipped where there is no
    desktop.
    """
    return os.environ.get("QT_QPA_PLATFORM", "") != "offscreen"


def _explain(method: str) -> str:
    return {
        "pkexec": "Your desktop will ask for your password.",
        "askpass": "You will be asked for your password.",
        "terminal": "A terminal window will open and ask for your password.",
        "sudo": "sudo will ask for your password in the terminal you started "
                "CanLab from.",
        "none": "No way to ask for a password was found, so you will have to "
                "run this yourself.",
    }.get(method, "")


def _ask(parent, title: str, body: str, argv: list[str],
         extra: QCheckBox | None = None) -> bool:
    """Show what will be run, and let the user decline."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(body)
    box.setInformativeText(
        f"CanLab will run:\n  sudo {' '.join(argv)}\n\n"
        f"{_explain(pv.elevation_method())}")
    box.setStandardButtons(QMessageBox.StandardButton.Yes
                           | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Yes)
    if extra is not None:
        box.setCheckBox(extra)
    return box.exec() == QMessageBox.StandardButton.Yes


def ensure_socketcan_up(parent, device: str, bitrate: int, *,
                        fd: bool = False, data_bitrate: int | None = None) -> tuple[bool, str]:
    """Make sure `device` is up, asking for rights if it is not.

    Returns (ready, message). `ready` False means do not try to open the bus:
    either the user declined, or the command failed and the message says how.
    """
    state = pv.interface_state(device)
    if not interactive():
        # Nobody to ask: report what is true and let the caller try to open it,
        # which is what happened before any of this existed.
        return True, (f"{device} is {'up' if state['up'] else 'down'} "
                      f"(no prompt: running without a desktop)")

    if not state["exists"]:
        others = [d for d in pv.can_devices() if d != device]
        hint = (f"\n\nCAN devices on this machine: {', '.join(others)}"
                if others else
                "\n\nThere are no CAN devices at all. If your adapter needs a "
                "kernel driver it may not be loaded; if it is a USB stick it "
                "probably uses the slcan or gs_usb backend instead of "
                "SocketCAN.")
        return False, f"{device} does not exist.{hint}"

    if state["up"]:
        running = state.get("bitrate")
        if running and int(running) != int(bitrate):
            return True, (f"{device} is up at {running:,} bit/s, but this adapter "
                          f"is configured for {int(bitrate):,}. The kernel's "
                          f"setting wins; change it or the adapter to match.")
        return True, f"{device} is up" + (f" at {running:,} bit/s" if running else "")

    if state["kind"] == "vcan":
        argv = ["ip", "link", "set", "up", device]
        body = f"{device} is a virtual CAN device and is down."
        listen = None
    else:
        listen = QCheckBox("Listen only: never acknowledge frames "
                           "(safest on a vehicle; not every controller supports it)")
        listen.setChecked(False)
        argv = pv.bring_up_command(device, bitrate, fd=fd, data_bitrate=data_bitrate)
        body = (f"{device} is down, so it cannot be opened yet.\n\n"
                f"The kernel owns a CAN device's bitrate, and setting it needs "
                f"administrator rights. This is needed once per device, not for "
                f"the capture itself.")

    if not _ask(parent, "Bring the CAN interface up", body, argv, listen):
        return False, "Cancelled. The interface is still down."

    if listen is not None and listen.isChecked():
        argv = pv.bring_up_command(device, bitrate, listen_only=True,
                                   fd=fd, data_bitrate=data_bitrate)

    ok, output = pv.run_elevated(argv)
    if not ok and "listen-only" in " ".join(argv) and "not supported" in output.lower():
        # Some controllers reject the mode outright. Say so rather than
        # leaving the user with a failure they cannot interpret.
        return False, ("This controller does not support listen-only mode.\n\n"
                       + output)
    if not ok:
        return False, output

    state = pv.interface_state(device)
    if not state["up"]:
        return False, (f"The command reported success but {device} is still down."
                       f"\n\n{state['detail']}")
    note = " in listen-only mode" if state["listen_only"] else ""
    return True, f"{device} is up at {state['bitrate'] or bitrate:,} bit/s{note}"


def confirm_not_silent(parent, adapter, *, remember: dict | None = None) -> bool:
    """Say what opening this adapter does to the bus, before it is opened.

    Test reported this and Connect did not, which is the wrong way round: Test
    is a deliberate experiment, while Connect is what someone presses with the
    adapter already wired to a vehicle.
    """
    from canlab.core.adapters import _silence

    silent, warning = _silence(adapter)
    if silent or not warning or not interactive():
        return True
    if remember is not None and remember.get(adapter.interface):
        return True

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("This adapter cannot listen silently")
    box.setText(warning)
    box.setInformativeText(
        "CanLab will not send any frames. What the controller does in hardware "
        "is not under its control:\n\n"
        "\u2022 at the right bitrate it acknowledges frames, which is what any "
        "scan tool or diagnostic dongle also does;\n"
        "\u2022 at the wrong bitrate it emits error flags until it goes "
        "bus-off, which can disturb traffic while that lasts.\n\n"
        "Only connect to a vehicle that is parked and that nobody is about to "
        "drive.")
    ask_again = QCheckBox("Do not warn me again for this backend")
    box.setCheckBox(ask_again)
    box.setStandardButtons(QMessageBox.StandardButton.Ok
                           | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    accepted = box.exec() == QMessageBox.StandardButton.Ok
    if accepted and remember is not None and ask_again.isChecked():
        remember[adapter.interface] = True
    return accepted


def create_virtual_bus(parent, device: str = "vcan0") -> tuple[bool, str]:
    """Create a virtual CAN device, for trying the application with no hardware."""
    state = pv.interface_state(device)
    if state["exists"] and state["up"]:
        return True, f"{device} already exists and is up"

    commands = pv.virtual_bus_commands(device)
    body = (f"This creates {device}, a virtual CAN bus inside the kernel. "
            f"Nothing physical is touched and no vehicle is involved: it is "
            f"the standard way to try CAN software without hardware.\n\n"
            f"It disappears when the machine reboots.")
    joined = " && ".join(" ".join(c) for c in commands)
    if not _ask(parent, f"Create {device}", body, ["sh", "-c", joined]):
        return False, "Cancelled."

    ok, output = pv.run_elevated(["sh", "-c", joined])
    if not ok:
        return False, output
    state = pv.interface_state(device)
    if not state["exists"]:
        return False, output or f"{device} was not created"
    return True, (f"{device} is ready. Select SocketCAN with channel {device}, "
                  f"then generate traffic with:  cangen {device} -g 5 -I i -L 8")


def install_udev_rule(parent) -> tuple[bool, str]:
    """Let USB CAN adapters be opened without root, by group permission."""
    commands = pv.install_udev_rule_commands()
    joined = " && ".join(" ".join(c) for c in commands)
    names = ", ".join(label for _v, _p, label in pv.UDEV_VENDORS)
    body = (f"Some USB CAN adapters can only be opened by root, which is why "
            f"they work under sudo and not otherwise. This installs a udev "
            f"rule giving the plugdev group access to them:\n\n  {names}\n\n"
            f"Unplug and replug the adapter afterwards. Delete "
            f"{pv.UDEV_PATH} to undo it.")
    if not _ask(parent, "Allow USB CAN adapters without root", body,
                ["sh", "-c", joined]):
        return False, "Cancelled."
    ok, output = pv.run_elevated(["sh", "-c", joined])
    if not ok:
        return False, output
    if not pv._current_groups() or "plugdev" not in pv._current_groups():
        return True, ("Rule installed, but you are not in the plugdev group. "
                      "Run:  sudo usermod -aG plugdev $USER\nThen log out and "
                      "back in, and replug the adapter.")
    return True, "Rule installed. Unplug and replug the adapter."
