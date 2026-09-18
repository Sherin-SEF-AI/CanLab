# Capture kit

A headless logger for a small computer left in a vehicle: a Raspberry Pi
with a CAN HAT, or any Linux box with a SocketCAN adapter. It records the
bus into rotating SavvyCAN CSV segments, takes event marks from the
keyboard, from HTTP or from GPIO switches, and folds everything into one
`.canlab` project the desktop opens with the marks on the timeline.

It is receive only. It never sends a frame and never asks for privileges:
if the interface is down it prints the `ip link` command and exits.

## Install

```bash
pip install --user canlab        # or pip install --user -e . from a checkout
canlab-cli capture --help
```

`python-can` is required. `gpiozero` is optional and only needed for
`--gpio`.

## Run by hand

```bash
sudo ip link set can0 up type can bitrate 500000 listen-only on
canlab-cli capture --interface socketcan --channel can0 --bitrate 500000 \
    --keys b=brake,h=horn --duration 600
```

Press `b` to open a `brake` mark and `b` again to close it; `q` stops.
Without `--keys`, type a label and Enter (`brake`, `brake off`, `brake
point`). Ctrl-C stops cleanly: open marks are closed, the last segment is
flushed, and the project is written.

## Marks from a phone

```bash
canlab-cli capture --interface socketcan --channel can0 \
    --http 8765 --http-host 0.0.0.0 --token-file ~/.canlab/kit-token
```

The token is generated on the first run and written to the file. From any
device on the same network:

```bash
curl -X POST -H "X-API-Token: $(cat ~/.canlab/kit-token)" \
     -H "Content-Type: application/json" \
     -d '{"label": "brake"}' http://kit.local:8765/mark
```

`action` may be `toggle` (default), `begin`, `end` or `point`. `GET /frames`
returns the newest frames and `GET /status` the frame count. `/inject` is
not served by the kit.

Binding to anything but loopback is announced at start: anyone on that
network with the token can read frames and add marks.

## Marks from a switch

```bash
canlab-cli capture ... --gpio 17=brake,27=horn
```

A button between the pin and ground (gpiozero's default pull-up) marks
`brake` for as long as it is held. Without gpiozero the kit says so and
records without the switches.

## As a service

`canlab-capture.service` is a unit for a Pi: it brings `can0` up in
listen-only mode, runs the kit as user `pi` with HTTP marks and one GPIO
pin, and restarts on failure. Each start records into a new directory under
`/home/pi/captures`; stopping the service (or the ignition, if the Pi is on
a switched supply and shuts down cleanly) writes the project.

Copy the project to the desktop and open it with File, Open Project. Every
mark is on the INTELLIGENCE tab's list and the TIMELINE, and "Rank
candidates" finds the bytes that follow a mark.

## What has and has not been tested

The kit is exercised in the test suite on python-can's `virtual` backend and
on a recording fake bus, including marks from stdin, HTTP and a fake
`gpiozero`. It has not yet been run on a Pi in a car; the systemd unit is a
starting point, not a report.
