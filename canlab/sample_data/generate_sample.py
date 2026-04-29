#!/usr/bin/env python3
"""Generate a realistic Hyundai Kona CAN sample dataset."""
import csv
import math
import random

random.seed(42)

MESSAGES = {
    0x018: {"name": "MDPS12",      "freq": 100, "type": "sensor"},
    0x02C: {"name": "BRAKE11",     "freq": 100, "type": "sensor"},
    0x050: {"name": "LKAS11",      "freq": 100, "type": "status"},
    0x0A6: {"name": "WHL_SPD11",   "freq": 50,  "type": "sensor"},
    0x251: {"name": "MDPS11",      "freq": 100, "type": "sensor"},
    0x260: {"name": "SAS11",       "freq": 100, "type": "sensor"},
    0x316: {"name": "TCS13",       "freq": 50,  "type": "status"},
    0x544: {"name": "CLU11",       "freq": 50,  "type": "counter"},
    0x593: {"name": "TPMS11",      "freq": 1,   "type": "diagnostic"},
    0x4F1: {"name": "CLUSTER11",   "freq": 10,  "type": "sensor"},
}

TOTAL_TIME = 10.0
rows = []

for msg_id, info in MESSAGES.items():
    freq  = info["freq"]
    mtype = info["type"]
    n     = int(TOTAL_TIME * freq)
    counter = 0
    speed = 0.0
    steer = 0.0

    for i in range(n):
        ts = i / freq
        data = [0] * 8

        if mtype == "counter":
            data[0] = (counter & 0x0F) << 4
            counter = (counter + 1) & 0x0F
        elif mtype == "sensor":
            if msg_id == 0x018:  # steering
                steer = 30.0 * math.sin(2 * math.pi * ts / 3.0)
                raw = int((steer + 1800) * 10) & 0xFFFF
                data[0] = (raw >> 8) & 0xFF
                data[1] = raw & 0xFF
                data[2] = random.randint(0, 3)
                data[7] = sum(data[:7]) & 0xFF
            elif msg_id == 0x0A6:  # wheel speed
                speed = 60.0 + 20.0 * math.sin(2 * math.pi * ts / 5.0)
                raw = int(speed * 32)
                for j in range(4):
                    data[j*2]     = (raw >> 8) & 0xFF
                    data[j*2 + 1] = raw & 0xFF
            elif msg_id == 0x4F1:  # cluster (vehicle speed)
                vspeed = max(0, int(speed))
                data[0] = (counter & 0x0F) << 4
                data[1] = (vspeed >> 2) & 0xFF
                data[2] = (vspeed & 0x03) << 6
                counter = (counter + 1) & 0x0F
                data[7] = sum(data[:7]) & 0xFF
            else:
                val = int(128 + 60 * math.sin(2 * math.pi * ts / 2.0 + msg_id * 0.1))
                data[0] = (counter & 0x0F) << 4
                counter = (counter + 1) & 0x0F
                data[1] = val & 0xFF
                data[2] = random.randint(0, 255)
                data[7] = sum(data[:7]) & 0xFF
        elif mtype == "status":
            data[0] = 0x40 if ts > 2 else 0x00
            data[1] = 0x01
            data[7] = sum(data[:7]) & 0xFF
        elif mtype == "diagnostic":
            data[0] = 0xB2  # tire pressure
            data[1] = 0xB4
            data[2] = 0xB3
            data[3] = 0xB5

        rows.append({
            "ts":  ts,
            "id":  msg_id,
            "dlc": 8,
            "bus": 0,
            "data": data,
        })

rows.sort(key=lambda r: r["ts"])

output = "sample_kona_drive.csv"
with open(output, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["Time Stamp", "ID", "Extended", "Dir", "Bus", "LEN",
                     "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"])
    for r in rows:
        ts_us = int(r["ts"] * 1_000_000)
        d = r["data"]
        writer.writerow([
            ts_us,
            format(r["id"], "03X"),
            "false", "Rx", r["bus"], r["dlc"],
            d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7],
        ])

print(f"Generated {len(rows)} frames → {output}")
