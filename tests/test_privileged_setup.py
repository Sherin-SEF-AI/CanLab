"""Asking for root the way a desktop asks, and only where root is needed.

A SocketCAN device's bitrate belongs to the kernel, so the first thing a user
with a new adapter meets is "Network is down" and an instruction to go and
type something in a terminal. These are the pieces that replace that: what to
run, how to ask, and what the kernel says about the device beforehand.

Nothing here runs anything as root. The commands are built and inspected; the
asking is a program whose job is to ask, and no password ever reaches CanLab.
"""
import pytest

from canlab.core import privileged as pv


# ── the command that brings an interface up ──────────────────────────────────

def test_bring_up_sets_the_bitrate_the_kernel_owns():
    assert pv.bring_up_command("can0", 500_000) == [
        "ip", "link", "set", "can0", "up", "type", "can", "bitrate", "500000"]


def test_listen_only_is_asked_for_explicitly():
    """The only way to be certain attaching to a vehicle changes nothing."""
    argv = pv.bring_up_command("can0", 250_000, listen_only=True)
    assert argv[-2:] == ["listen-only", "on"]
    assert "250000" in argv


def test_can_fd_carries_both_bitrates():
    argv = pv.bring_up_command("can0", 500_000, fd=True, data_bitrate=2_000_000)
    assert "dbitrate" in argv and "2000000" in argv and argv[argv.index("fd") + 1] == "on"


def test_a_virtual_bus_is_three_commands():
    got = pv.virtual_bus_commands("vcan0")
    assert got[0] == ["modprobe", "vcan"]
    assert "vcan" in got[1] and "vcan0" in got[1]
    assert got[2][-1] == "vcan0"


# ── choosing how to ask ──────────────────────────────────────────────────────

@pytest.fixture
def desktop(monkeypatch):
    """A graphical session with a chosen set of programs installed."""
    def configure(programs: set[str], askpass: str = ""):
        monkeypatch.setattr(pv, "_graphical", lambda: True)
        monkeypatch.setattr(pv, "_has", lambda p: p in programs)
        monkeypatch.setattr(pv, "_askpass", lambda: askpass)
        monkeypatch.setattr(pv.sys, "platform", "linux")
    return configure


def test_polkit_is_preferred(desktop):
    """PolicyKit's own dialog is what a desktop application should use: the
    session draws the prompt and the password never reaches this process."""
    desktop({"pkexec", "sudo", "xterm"}, askpass="/usr/bin/ssh-askpass")
    assert pv.elevation_method() == "pkexec"
    assert pv.elevated_command(["ip", "link"])[:2] == ["pkexec", "--disable-internal-agent"]


def test_a_graphical_askpass_comes_next(desktop):
    desktop({"sudo", "xterm"}, askpass="/usr/bin/ssh-askpass")
    assert pv.elevation_method() == "askpass"


def test_then_a_terminal_window(desktop):
    desktop({"xterm"})
    assert pv.elevation_method() == "terminal"
    assert pv.elevated_command(["ip", "link"]) == ["xterm", "-e", "sudo", "ip", "link"]


def test_nothing_available_is_reported_rather_than_guessed(desktop, monkeypatch):
    desktop(set())
    monkeypatch.setattr(pv, "_graphical", lambda: False)
    monkeypatch.setattr(pv.sys, "stdin", None)
    assert pv.elevation_method() == "none"
    ok, message = pv.run_elevated(["ip", "link", "set", "can0", "up"])
    assert ok is False
    assert "sudo ip link set can0 up" in message


def test_a_cancelled_dialog_is_not_an_error(desktop, monkeypatch):
    """Declining is an ordinary outcome, so it must not raise or look like a bug."""
    desktop({"pkexec"})

    class Done:
        returncode, stdout, stderr = 126, "", ""
    monkeypatch.setattr(pv.subprocess, "run", lambda *a, **k: Done())
    ok, message = pv.run_elevated(["ip", "link"])
    assert ok is False and "ancel" in message


def test_the_password_never_passes_through_canlab():
    source = (pv.__file__ and open(pv.__file__).read()) or ""
    for word in ("getpass", "stdin.write", "input(", "password="):
        assert word not in source, f"{word} suggests handling the password here"


# ── reading the device, which needs no rights at all ─────────────────────────

def test_interface_state_parses_a_configured_device(monkeypatch):
    text = ("3: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 16 qdisc pfifo_fast state UP "
            "mode DEFAULT group default qlen 10\n"
            "    link/can  promiscuity 0 minmtu 0 maxmtu 0 \n"
            "    can state ERROR-ACTIVE restart-ms 0 \n"
            "    bitrate 500000 sample-point 0.875 \n")

    class Done:
        returncode, stdout, stderr = 0, text, ""
    monkeypatch.setattr(pv.shutil, "which", lambda p: "/bin/ip")
    monkeypatch.setattr(pv.sys, "platform", "linux")
    monkeypatch.setattr(pv.subprocess, "run", lambda *a, **k: Done())
    state = pv.interface_state("can0")
    assert state["exists"] and state["up"] and state["bitrate"] == 500_000
    assert state["listen_only"] is False


def test_interface_state_sees_listen_only(monkeypatch):
    text = ("3: can0: <NOARP,UP,LOWER_UP> mtu 16 state UP\n"
            "    can <LISTEN-ONLY> state ERROR-ACTIVE \n"
            "    bitrate 250000 \n")

    class Done:
        returncode, stdout, stderr = 0, text, ""
    monkeypatch.setattr(pv.shutil, "which", lambda p: "/bin/ip")
    monkeypatch.setattr(pv.sys, "platform", "linux")
    monkeypatch.setattr(pv.subprocess, "run", lambda *a, **k: Done())
    assert pv.interface_state("can0")["listen_only"] is True


def test_a_missing_device_is_reported_as_missing(monkeypatch):
    class Done:
        returncode, stdout, stderr = 1, "", 'Device "can9" does not exist.'
    monkeypatch.setattr(pv.shutil, "which", lambda p: "/bin/ip")
    monkeypatch.setattr(pv.sys, "platform", "linux")
    monkeypatch.setattr(pv.subprocess, "run", lambda *a, **k: Done())
    state = pv.interface_state("can9")
    assert state["exists"] is False and "can9" in state["detail"]


# ── USB adapters need permission, not root ───────────────────────────────────

def test_the_udev_rule_covers_the_analyser_that_started_this():
    rule = pv.udev_rule()
    assert '04d8' in rule and '0053' in rule, "CANalyst-II is not covered"
    assert 'GROUP="plugdev"' in rule, "a group is safer than making it world-writable"
    assert 'MODE="0666"' not in rule


def test_installing_the_rule_reloads_udev():
    commands = pv.install_udev_rule_commands()
    joined = [" ".join(c) for c in commands]
    assert any(pv.UDEV_PATH in c for c in joined)
    assert any("udevadm control --reload-rules" in c for c in joined)
    assert any("udevadm trigger" in c for c in joined)
