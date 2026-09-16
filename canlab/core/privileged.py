"""Getting a CAN interface up, which on Linux needs root, without a terminal.

A SocketCAN device is configured by the kernel, not by the application: until
somebody runs ``ip link set can0 up type can bitrate 500000``, opening it fails
with "Network is down" no matter what the application does. That command needs
root. Telling the user to go and run it themselves in a terminal is how this
used to work, and it is a poor answer for someone who has just plugged in an
adapter and pressed Connect.

So the application asks for the password the way the desktop does, in this
order:

1. ``pkexec``: PolicyKit's own dialog. This is what a desktop application is
   supposed to use. The prompt is drawn by the session, not by us, and the
   password never passes through this process.
2. ``sudo -A`` with a graphical askpass helper, for desktops with sudo
   configured but no PolicyKit agent.
3. ``sudo`` inside a terminal window, which is the "terminal popup" fallback.
4. plain ``sudo``, when there is a controlling terminal, as when the
   application was started from a shell.

Nothing here ever handles the password itself. There is no place in CanLab
that reads, stores or forwards one: every route hands the job to a program
whose purpose is to ask.

Root is needed for the interface, not for the capture. Once a device is up,
reading frames from it needs no privileges at all, which is why this is a
one-time prompt per device rather than something the live capture depends on.
USB adapters that are not SocketCAN (slcan sticks, GVRET boards, CANalyst-II)
need no root either, only permission on the device node: see `udev_rule`.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)

#: Terminal emulators to try for the fallback, with the flag that runs a
#: command. Ordered so a desktop's own terminal is preferred.
TERMINALS = (
    ("x-terminal-emulator", "-e"),
    ("gnome-terminal", "--"),
    ("konsole", "-e"),
    ("xfce4-terminal", "-x"),
    ("kitty", "--"),
    ("alacritty", "-e"),
    ("xterm", "-e"),
)

ASKPASS_HELPERS = (
    "/usr/bin/ksshaskpass",
    "/usr/lib/ssh/x11-ssh-askpass",
    "/usr/lib/openssh/gnome-ssh-askpass",
    "/usr/libexec/openssh/ssh-askpass",
    "/usr/bin/ssh-askpass",
)


def _has(program: str) -> bool:
    return shutil.which(program) is not None


def _graphical() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _askpass() -> str:
    env = os.environ.get("SUDO_ASKPASS")
    if env and os.path.exists(env):
        return env
    return next((p for p in ASKPASS_HELPERS if os.path.exists(p)), "")


def elevation_method() -> str:
    """Which route would be used: pkexec, askpass, terminal, sudo or none."""
    if sys.platform != "linux":
        return "none"
    if _graphical() and _has("pkexec"):
        return "pkexec"
    if _has("sudo") and _graphical() and _askpass():
        return "askpass"
    if _graphical() and any(_has(t) for t, _ in TERMINALS):
        return "terminal"
    if _has("sudo") and sys.stdin is not None and sys.stdin.isatty():
        return "sudo"
    return "none"


def elevated_command(argv: list[str], method: str | None = None) -> list[str]:
    """The command to actually run, wrapped for the chosen method."""
    method = method or elevation_method()
    if method == "pkexec":
        # --disable-internal-agent: fail rather than silently fall back to a
        # text prompt on a process with no terminal, which would hang.
        return ["pkexec", "--disable-internal-agent", *argv]
    if method == "askpass":
        return ["sudo", "-A", "--", *argv]
    if method == "terminal":
        for term, flag in TERMINALS:
            if _has(term):
                return [term, flag, "sudo", *argv]
    if method == "sudo":
        return ["sudo", *argv]
    return list(argv)


def run_elevated(argv: list[str], timeout: float = 120.0) -> tuple[bool, str]:
    """Run `argv` as root, prompting however this desktop prompts.

    Returns (ok, output). A cancelled password dialog is a failure with a
    message saying so, not an exception: the user declining is an ordinary
    outcome, not an error in the program.
    """
    method = elevation_method()
    if method == "none":
        return False, ("No way to ask for administrator rights was found. "
                       "Run this yourself:\n  sudo " + " ".join(argv))
    command = elevated_command(argv, method)
    env = dict(os.environ)
    if method == "askpass":
        env["SUDO_ASKPASS"] = _askpass()
        command = ["sudo", "-A", *argv]
    log.info("elevating via %s: %s", method, " ".join(argv))
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, "The password prompt timed out."
    except OSError as e:
        return False, f"Could not start {command[0]}: {e}"
    output = (done.stdout + done.stderr).strip()
    if done.returncode == 0:
        return True, output
    if method == "pkexec" and done.returncode == 126:
        return False, "Cancelled: the password dialog was dismissed."
    if method == "pkexec" and done.returncode == 127:
        return False, "Not authorised to run that command."
    return False, output or f"{command[0]} exited with {done.returncode}"


# ── SocketCAN ────────────────────────────────────────────────────────────────

def bring_up_command(device: str, bitrate: int, *, listen_only: bool = False,
                     fd: bool = False, data_bitrate: int | None = None) -> list[str]:
    """The `ip link` command that brings a CAN device up.

    `listen_only` puts the controller in the mode where it does not
    acknowledge frames, which is the only way to be certain that attaching to
    somebody's vehicle changes nothing on the bus. Not every controller
    supports it; the caller should fall back and say so rather than pretend.
    """
    argv = ["ip", "link", "set", device, "up", "type", "can", "bitrate", str(int(bitrate))]
    if fd and data_bitrate:
        argv += ["dbitrate", str(int(data_bitrate)), "fd", "on"]
    if listen_only:
        argv += ["listen-only", "on"]
    return argv


def down_command(device: str) -> list[str]:
    return ["ip", "link", "set", device, "down"]


def virtual_bus_commands(device: str = "vcan0") -> list[list[str]]:
    """Create and start a virtual CAN device, for testing with no hardware."""
    return [["modprobe", "vcan"],
            ["ip", "link", "add", "dev", device, "type", "vcan"],
            ["ip", "link", "set", "up", device]]


def interface_state(device: str) -> dict:
    """What the kernel says about a CAN device: presence, state, bitrate.

    Returns ``{"exists", "up", "bitrate", "listen_only", "kind", "detail"}``.
    Reading this needs no privileges, so the application can tell the user
    exactly what is wrong before asking for any.
    """
    out = {"exists": False, "up": False, "bitrate": None, "listen_only": False,
           "kind": "", "detail": ""}
    if sys.platform != "linux" or not shutil.which("ip"):
        out["detail"] = "not Linux, or iproute2 is missing"
        return out
    try:
        done = subprocess.run(["ip", "-details", "link", "show", device],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        out["detail"] = str(e)
        return out
    if done.returncode != 0:
        out["detail"] = (done.stderr or "").strip() or f"{device} not found"
        return out
    text = done.stdout
    out["exists"] = True
    out["detail"] = text.strip()
    header = text.splitlines()[0] if text else ""
    # "state UP" is the link state; a CAN device that is configured but with no
    # bus attached reports UNKNOWN, which still counts as up for opening it.
    out["up"] = ("state UP" in header or "state UNKNOWN" in header) and "NO-CARRIER" not in header
    for line in text.splitlines():
        parts = line.split()
        if "bitrate" in parts:
            try:
                out["bitrate"] = int(parts[parts.index("bitrate") + 1])
            except (ValueError, IndexError):
                pass
        if "vcan" in parts:
            out["kind"] = "vcan"
        elif "can" in parts and not out["kind"]:
            out["kind"] = "can"
        if "LISTEN-ONLY" in line.upper():
            out["listen_only"] = True
    return out


def can_devices() -> list[str]:
    """Every CAN or vcan network device the kernel currently has."""
    try:
        return sorted(p.name for p in __import__("pathlib").Path("/sys/class/net").iterdir()
                      if (p / "type").exists() and (p / "type").read_text().strip() == "280")
    except OSError:
        return []


# ── USB adapters, which need permission rather than root ─────────────────────

#: USB adapters that present a raw device rather than a network interface.
#: Without a rule, libusb sees them only as root, which is why a CANalyst-II
#: works under sudo and not otherwise.
UDEV_VENDORS = (
    ("04d8", "0053", "CANalyst-II"),
    ("1d50", "606f", "candleLight / CANable / CANtact"),
    ("0483", "1234", "8devices USB2CAN"),
    ("16d0", "117e", "CANable 2.0"),
)

UDEV_PATH = "/etc/udev/rules.d/99-canlab-can.rules"


def udev_rule() -> str:
    """A rule granting the plugdev group access to the USB CAN adapters.

    Group access rather than 0666: everyone on the machine being able to talk
    to a CAN adapter is not a sensible default, and every desktop already has
    a group for exactly this.
    """
    lines = ["# Installed by CanLab. Lets a user in the plugdev group open USB",
             "# CAN adapters without root. Remove this file to undo it.",
             ""]
    for vid, pid, label in UDEV_VENDORS:
        lines.append(f'# {label}')
        lines.append(
            f'SUBSYSTEM=="usb", ATTRS{{idVendor}}=="{vid}", '
            f'ATTRS{{idProduct}}=="{pid}", MODE="0660", GROUP="plugdev", TAG+="uaccess"')
    return "\n".join(lines) + "\n"


def install_udev_rule_commands() -> list[list[str]]:
    """Write the rule and reload udev, as a list of commands to run as root."""
    rule = udev_rule().replace("'", "'\\''")
    return [["sh", "-c", f"printf '%s' '{rule}' > {UDEV_PATH}"],
            ["udevadm", "control", "--reload-rules"],
            ["udevadm", "trigger"]]


def in_group(name: str = "plugdev") -> bool:
    try:
        import grp
        return name in [g.gr_name for g in grp.getgrall()
                        if os.getlogin() in g.gr_mem] or name in _current_groups()
    except Exception:
        return False


def _current_groups() -> list[str]:
    try:
        import grp
        return [grp.getgrgid(g).gr_name for g in os.getgroups()]
    except Exception:
        return []
