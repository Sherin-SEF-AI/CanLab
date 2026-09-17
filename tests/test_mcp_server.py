"""The MCP tool set, and the two transports an assistant reaches it through.

The tool logic is exercised directly, then over Streamable HTTP with the
official client, then through the stdio bridge (``canlab-mcp --attach``) in a
subprocess, which is how Claude Desktop reaches the server inside the window.
"""
import asyncio
import os
import socket
import subprocess
import sys
import threading

import pytest

from canlab.core.mcp_tools import CanLabTools, HeadlessBackend, clean

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "canlab", "sample_data", "sample_kona_drive.csv")


@pytest.fixture(scope="module")
def tools():
    t = CanLabTools(HeadlessBackend())
    t.load_log(SAMPLE)
    return t


# ── tool logic ───────────────────────────────────────────────────────────────

def test_status_and_ids(tools):
    st = tools.status()
    assert st["mode"] == "headless" and st["frames"] > 0 and st["ids"] > 0
    ids = tools.list_ids(limit=5)
    assert len(ids) == 5 and ids[0]["frames"] >= ids[-1]["frames"]
    assert ids[0]["rate_hz"] > 0 and isinstance(ids[0]["changing_bytes"], list)


def test_requires_load_first():
    with pytest.raises(ValueError):
        CanLabTools(HeadlessBackend()).list_ids()
    with pytest.raises(FileNotFoundError):
        CanLabTools(HeadlessBackend()).load_log("/nonexistent/capture.csv")


def test_detectors_return_plain_json(tools):
    import json
    for name in ("detect_counters_checksums", "detect_flags", "infer_value_tables",
                 "detect_boundaries", "detect_multiplexers"):
        out = getattr(tools, name)()
        json.dumps(out)                      # no numpy scalars, no NaN
        assert isinstance(out, dict)
    top = tools.list_ids(limit=1)[0]["id"]
    stats = tools.byte_stats(top)
    assert stats["frames"] > 0 and "B0" in stats["bytes"]
    assert 0 <= stats["bytes"]["B0"]["entropy_bits"] <= 8
    frames = tools.frames(top, n=3)
    assert len(frames) == 3 and len(frames[0]["data"].split()) == 8


def test_run_all_and_draft(tools):
    report = tools.run_all_detectors()
    assert set(report["totals"]) >= {"counters", "checksums", "flags", "enumerations"}
    draft = tools.draft_dbc()
    assert draft["signals"] > 0 and draft["dbc"].startswith("VERSION")


def test_dbc_edit_validate_decode_export(tools, tmp_path):
    top = tools.list_ids(limit=1)[0]["id"]
    ok = tools.add_dbc_signal(top, "SPEED", 8, 16, scale=0.01, unit="km/h")
    assert ok["added"] and ok["signal"]["message_id"] == top
    dup = tools.add_dbc_signal(top, "SPEED", 8, 16)
    assert not dup["added"] and "already exists" in dup["errors"][0]
    bad = tools.add_dbc_signal(top, "BAD", 60, 16)
    assert not bad["added"] and bad["errors"]
    gear = tools.add_dbc_signal(top, "GEAR", 0, 3, value_table={"0": "PARK", "1": "DRIVE"})
    assert gear["added"] and gear["signal"]["value_table"] == {"0": "PARK", "1": "DRIVE"}

    one = tools.decode(top, "01 02 03 04 05 06 07 08")
    assert one["values"]["SPEED"] == pytest.approx(0x0302 * 0.01)
    assert one["values"]["GEAR"] == 1
    last = tools.decode(top, n=2)
    assert len(last["frames"]) == 2 and "SPEED" in last["frames"][0]["values"]

    out = tmp_path / "out.dbc"
    assert tools.export_dbc(str(out))["written"]
    import cantools
    db = cantools.database.load_file(str(out))
    assert {s.name for s in db.get_message_by_frame_id(int(top, 16)).signals} == {"SPEED", "GEAR"}

    assert tools.remove_dbc_signal(top, "GEAR")["removed"]
    assert not tools.remove_dbc_signal(top, "GEAR")["removed"]
    assert tools.decode("7FF")["error"]


def test_annotations_rank(tools):
    t0 = tools.frames(tools.list_ids(limit=1)[0]["id"], n=1, from_end=False)[0]["t"]
    assert not tools.add_annotation("press", t0 + 5, t0 + 1)["added"]
    assert tools.add_annotation("press", t0 + 1, t0 + 4)["added"]
    assert tools.list_annotations()[0]["label"] == "press"
    ranked = tools.rank_annotations(top=5)
    assert isinstance(ranked, list)
    if ranked:
        assert 0 < ranked[0]["strength"] <= 1 and "summary" in ranked[0]


def test_search_and_fetch_contract(tools):
    top = tools.list_ids(limit=1)[0]["id"]
    res = tools.search("")["results"]
    assert res[0]["id"] == "status" and any(r["id"] == f"id:{top}" for r in res)
    hits = tools.search(top)["results"]
    assert hits and all({"id", "title", "url"} <= set(h) for h in hits)
    assert any(h["id"] == f"signal:{top}/SPEED" for h in tools.search("speed")["results"])
    doc = tools.fetch(f"id:{top}")
    assert {"id", "title", "text", "url", "metadata"} <= set(doc)
    assert "Byte statistics" in doc["text"] and "Last frames" in doc["text"]
    assert tools.fetch(f"canlab://id/{top}")["id"] == f"id:{top}"
    assert tools.fetch("status")["metadata"]["frames"] > 0
    assert tools.fetch(f"signal:{top}/SPEED")["metadata"]["length"] == 16
    with pytest.raises(ValueError):
        tools.fetch("nope")


def test_clean_converts_numpy():
    import numpy as np
    out = clean({"a": np.int64(3), "b": np.float32(1.5), "c": np.array([1, 2]),
                 "d": float("nan"), "e": np.bool_(True), 7: "x"})
    assert out == {"a": 3, "b": 1.5, "c": [1, 2], "d": None, "e": True, "7": "x"}


# ── transports ───────────────────────────────────────────────────────────────

def _json(result):
    """The tool's return value. A list comes back as structured content under
    "result"; a dict comes back as JSON text."""
    import json
    sc = result.structuredContent
    if sc is not None:
        return sc["result"] if set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def http_server():
    pytest.importorskip("mcp.server.fastmcp")
    import uvicorn

    from canlab.mcp_server import build_server, http_app
    t = CanLabTools(HeadlessBackend())
    t.load_log(SAMPLE)
    port = _free_port()
    mcp = build_server(t, port=port)
    config = uvicorn.Config(http_app(mcp, token="s3cret"), host="127.0.0.1",
                            port=port, log_level="error")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    import time
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}/mcp", "s3cret"
    server.should_exit = True
    th.join(5)


def test_streamable_http_roundtrip(http_server):
    url, token = http_server
    import httpx
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def go():
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
            async with streamable_http_client(url, http_client=http) as (r, w, _):
                async with ClientSession(r, w) as s:
                    init = await s.initialize()
                    names = {t.name for t in (await s.list_tools()).tools}
                    res = await s.call_tool("list_ids", {"limit": 3})
                    st = await s.call_tool("status", {})
                    return init.serverInfo.name, names, res, st

    name, names, res, st = asyncio.run(go())
    assert name == "canlab"
    assert set(CanLabTools.TOOL_NAMES) <= names
    assert not res.isError and len(_json(res)) == 3
    assert _json(st)["frames"] > 0


def test_http_rejects_bad_token(http_server):
    url, _ = http_server
    import httpx
    r = httpx.post(url, json={}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    r = httpx.post(url, json={})
    assert r.status_code == 401


def test_stdio_bridge_reaches_http_server(http_server):
    """Claude Desktop's path: it launches canlab-mcp --attach, which forwards
    to the HTTP server in the window."""
    url, token = http_server
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "canlab.mcp_server", "--attach", url, "--token", token],
        env=dict(os.environ, PYTHONPATH=os.getcwd()))

    async def go():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                res = await s.call_tool("byte_stats", {"can_id": "0x7FF"})
                ids = await s.call_tool("list_ids", {"limit": 2})
                return names, res, ids

    names, res, ids = asyncio.run(go())
    assert "add_dbc_signal" in names
    assert not res.isError and _json(res)["frames"] == 0
    assert len(_json(ids)) == 2


def test_bridge_fails_cleanly_when_nothing_listens():
    port = _free_port()
    proc = subprocess.run(
        [sys.executable, "-m", "canlab.mcp_server", "--attach", f"http://127.0.0.1:{port}/mcp"],
        capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PYTHONPATH=os.getcwd()))
    assert proc.returncode == 1 and "cannot reach" in proc.stderr


def test_cli_parser():
    from canlab.mcp_server import build_parser
    a = build_parser().parse_args(["--http", "--port", "9000", "--token", "x"])
    assert a.http and a.port == 9000 and a.token == "x" and not a.attach


def test_transport_tools_return_plain_json():
    """The reassembled messages and the PGN scan, as an assistant sees them."""
    import json

    import pandas as pd

    from canlab.core.mcp_tools import CanLabTools, HeadlessBackend

    cm = bytes([0x20, 0x13, 0x00, 0x03, 0xFF, 0xE1, 0xFE, 0x00])
    dt = [bytes([0x01, 0x03, 0x03, 0x60, 0x22, 0x4A, 0x80, 0x4D]),
          bytes([0x02, 0x27, 0x28, 0x2D, 0x2B, 0xB8, 0x42, 0x1D]),
          bytes([0x03, 0x60, 0x3B, 0x5D, 0x04, 0x19, 0xFF, 0xFF])]
    rows = [{"Timestamp": 0.0, "ID": "1CECFF0F", "Bus": 0, "DLC": 8, "Extended": True,
             **{f"B{k}": cm[k] for k in range(8)}}]
    rows += [{"Timestamp": 0.05 * (i + 1), "ID": "1CEBFF0F", "Bus": 0, "DLC": 8,
              "Extended": True, **{f"B{k}": d[k] for k in range(8)}}
             for i, d in enumerate(dt)]
    backend = HeadlessBackend()
    backend.df = pd.DataFrame(rows)
    tools = CanLabTools(backend)

    pgns = tools.list_pgns()
    assert any(r["pgn_name"].startswith("TP.CM") for r in pgns)
    messages = tools.list_transport_messages()
    assert len(messages) == 1
    m = messages[0]
    assert m["pgn"] == 0xFEE1 and m["count"] == 1 and m["bytes"] == 19
    assert m["transport"] == "BAM"
    json.dumps(messages)                                   # clean() did its job
    assert tools.list_transport_messages(pgn=0xF004) == []
    for name in ("list_pgns", "list_transport_messages"):
        assert name in CanLabTools.TOOL_NAMES
        assert not any(w in name for w in ("send", "inject", "transmit", "replay", "fuzz"))


def test_calibrate_reads_named_columns_and_reports_the_lag(tools, tmp_path):
    import numpy as np
    t = np.arange(0, 10, 0.1)
    speed = 60 + 20 * np.sin(2 * np.pi * t / 5)
    p = tmp_path / "gps.csv"
    p.write_text("time,speed (km/h),junk (x)\n" + "".join(
        f"{a + 1.0:.1f},{b:.3f},{i % 7}\n" for i, (a, b) in enumerate(zip(t, speed))))
    out = tools.calibrate(str(p), top_k=4, series="speed", lag_window_s=2.0)
    assert out and all(c["series"] == "speed" for c in out)
    best = out[0]
    assert best["id"] == "0A6" and best["verdict"] == "PASS"
    assert abs(best["lag_s"] - 1.0) < 0.2 and best["unit"] == "km/h"
    with pytest.raises(ValueError):
        tools.calibrate(str(p), series="nothing")
